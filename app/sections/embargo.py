"""Country-office embargo notice and submission template dialog."""
from dash import ctx, Input, no_update, Output, State

from dash_app import app


# ── Embargo ──────────────────────────────────────────────────────────────────

# Mirrors scripts/validate_co_submission.py. Clientside so a country office
# needs nothing installed and no file ever leaves their machine — the Python
# version stays for the GCHD team's own use on arrival. Keep the two in step:
# the rules are the coordinate ranges and the column aliases, both of which
# change rarely.
app.clientside_callback(
    """
    function(contents, filename) {
        if (!contents) { return ""; }
        var LON = ["longitude","lon","long","x"];
        var LAT = ["latitude","lat","y"];
        var NAME = ["name","facility_name","school_name","water_source"];
        var ID  = ["facility_id","id","code","school_id","facility_code","wpdx_id"];

        function esc(s){ return String(s).replace(/[<>&]/g, ""); }
        function report(errs, warns, nOk, total) {
            var h = "";
            errs.forEach(function(e){
                h += '<div class="co-v-err"><b>Error</b> ' + esc(e) + '</div>'; });
            warns.forEach(function(w){
                h += '<div class="co-v-warn"><b>Check</b> ' + esc(w) + '</div>'; });
            if (errs.length) {
                h += '<div class="co-v-sum co-v-bad">Not ready to send - '
                   + 'fix the errors above.</div>';
            } else if (warns.length) {
                h += '<div class="co-v-sum co-v-ok">' + nOk + ' of ' + total
                   + ' rows usable. Review the points above, then send.</div>';
            } else {
                h += '<div class="co-v-sum co-v-ok">All checks passed - '
                   + nOk + ' rows ready to send.</div>';
            }
            return h;
        }

        try {
            var raw = atob((contents.split(",")[1]) || "");
            // Strip a UTF-8 BOM: Excel writes one and it corrupts the first header.
            if (raw.charCodeAt(0) === 0xEF) { raw = raw.slice(3); }
            var lines = raw.split(/\\r\\n|\\n|\\r/).filter(function(l){
                return l.trim().length; });
            if (lines.length < 2) {
                return report(["The file has no data rows."], [], 0, 0);
            }
            // Split on commas outside quotes, so a quoted name with a comma
            // does not shift every column after it.
            function cells(line) {
                var out = [], cur = "", q = false;
                for (var i = 0; i < line.length; i++) {
                    var c = line[i];
                    if (c === '"') { q = !q; }
                    else if (c === "," && !q) { out.push(cur); cur = ""; }
                    else { cur += c; }
                }
                out.push(cur);
                return out.map(function(s){ return s.trim().replace(/^"|"$/g, ""); });
            }

            var hdr = cells(lines[0]).map(function(h){ return h.toLowerCase(); });
            function find(cands) {
                for (var i = 0; i < cands.length; i++) {
                    var j = hdr.indexOf(cands[i]);
                    if (j >= 0) { return j; }
                }
                return -1;
            }
            var iLon = find(LON), iLat = find(LAT),
                iName = find(NAME), iId = find(ID);

            var errs = [], warns = [];
            if (iLon < 0) { errs.push("No longitude column. Name it 'longitude' "
                                      + "(also accepted: lon, long, x)."); }
            if (iLat < 0) { errs.push("No latitude column. Name it 'latitude' "
                                      + "(also accepted: lat, y)."); }
            if (iName < 0) { warns.push("No name column - facilities will be "
                                        + "unlabelled."); }
            if (iId < 0) { warns.push("No facility_id column - ids will be "
                                      + "generated, so a re-submission cannot "
                                      + "update existing records."); }
            ["iso3","country","source","facility_type"].forEach(function(c){
                if (hdr.indexOf(c) >= 0) {
                    warns.push("Column '" + c + "' is ignored - country, type "
                             + "and source are set when the layer is registered.");
                }
            });
            if (errs.length) { return report(errs, warns, 0, lines.length - 1); }

            var nBad = 0, nLonR = 0, nLatR = 0, nNull = 0, nMulti = 0,
                nMetres = 0, nPos = 0, nNeg = 0, nOk = 0, seen = {}, nDup = 0;

            for (var r = 1; r < lines.length; r++) {
                var row = cells(lines[r]);
                var lo = parseFloat(row[iLon]), la = parseFloat(row[iLat]);
                if (isNaN(lo) || isNaN(la)) { nBad++; continue; }
                nOk++;
                if (Math.abs(lo) > 180) { nLonR++; }
                if (Math.abs(la) > 90)  { nLatR++; }
                if (Math.abs(lo) > 1000 || Math.abs(la) > 1000) { nMetres++; }
                if (lo === 0 && la === 0) { nNull++; }
                if (la > 0) { nPos++; } else if (la < 0) { nNeg++; }
                var key = lo + "," + la;
                if (seen[key]) { nDup++; } else { seen[key] = 1; }
                if (iName >= 0 && /[\\r\\n]/.test(row[iName] || "")) { nMulti++; }
            }

            var total = lines.length - 1;
            if (nBad) { warns.push(nBad + " of " + total + " rows have a missing "
                      + "or non-numeric coordinate and will be dropped."); }
            if (!nOk) { return report(["No row has a usable coordinate pair."],
                                      warns, 0, total); }
            if (nLonR) { errs.push(nLonR + " rows have longitude outside "
                       + "-180..180."); }
            if (nLatR) { errs.push(nLatR + " rows have latitude outside -90..90. "
                       + "If these look like longitudes, the two columns are "
                       + "swapped."); }
            if (nMetres) { errs.push("Coordinates look like metres, not degrees "
                         + "- reproject to WGS84 (EPSG:4326) before exporting."); }
            if (nNull) { warns.push(nNull + " rows sit at exactly 0,0 - usually "
                       + "a blank coordinate rather than a real location."); }
            if (nPos && nNeg) { warns.push("Latitudes span both hemispheres - "
                              + "check for a missing minus sign if the country "
                              + "is entirely north or south of the equator."); }
            if (nDup) { warns.push(nDup + " rows share a coordinate with another "
                      + "row - possible duplicates."); }
            if (nMulti) { warns.push(nMulti + " names contain a line break. "
                        + "These are collapsed automatically, but a line break "
                        + "can shift columns in some exports."); }

            return report(errs, warns, nOk, total);
        } catch (e) {
            return report(["Could not read that file as CSV: " + e.message],
                          [], 0, 0);
        }
    }
    """,
    Output("co-validate-report", "children"),
    Input("co-validate-upload",  "contents"),
    State("co-validate-upload",  "filename"),
    prevent_initial_call=True,
)


@app.callback(
    Output("co-template-gate",   "style"),
    Input("infra-add-layer-btn", "n_clicks"),
    Input("co-template-close",   "n_clicks"),
    prevent_initial_call=True,
)
def toggle_co_template_dialog(_open, _close):
    if ctx.triggered_id == "infra-add-layer-btn":
        return {"display": "flex"}
    return {"display": "none"}


@app.callback(
    Output("store-embargo-permanent", "data"),
    Output("embargo-gate",            "style"),
    Input("embargo-btn",              "n_clicks"),
    State("embargo-dismiss-forever",  "value"),
    prevent_initial_call=True,
)
def accept_embargo(n, dismiss_forever):
    """Dismiss the notice, persisting only if the box was ticked."""
    if not n:
        return no_update, no_update
    return bool(dismiss_forever), {"display": "none"}


@app.callback(
    Output("embargo-gate",           "style", allow_duplicate=True),
    Input("store-embargo-permanent", "data"),
    prevent_initial_call=True,
)
def restore_embargo_state(accepted_forever):
    """Hide on load only for users who ticked 'don't show this again'."""
    return {"display": "none"} if accepted_forever else no_update
