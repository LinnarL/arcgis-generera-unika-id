# -*- coding: utf-8 -*-
"""
GenerateUniqueIDs.pyt

Tilldelar sekventiella ID:n till ett fält i ett vektorlager. Fältet skapas om
det inte redan finns, annars skrivs befintliga värden över.

Sorteringen styr vilket objekt som får det lägsta ID:t: databasens egen
ordning, ett valfritt fält, longitud eller latitud för objektets centroid,
eller bådadera i kombination ("rad för rad" — uppifrån och ned, vänster till
höger inom varje rad, som läsordning i ett rutnät). Centroiden omvandlas
alltid till WGS84 (EPSG:4326) innan longitud/latitud läses av, så sorteringen
blir densamma oavsett vilket koordinatsystem lagret själv använder.

Rad-för-rad-sorteringen delar in objekten i rader utifrån antalet objekt och
centroidernas utbredning, och lägger radgränserna vid de största glappen
mellan på varandra följande latituder — se `_row_major_oids`. Det ger exakt
läsordning för punkter på ett regelbundet rutnät (provytor, mätpunkter); för
oregelbundet spridda punkter är det en rimlig approximation.

Krav: ArcGIS Pro 3.x (arcpy). Inga paket utöver Pythons standardbibliotek.
"""

import math

import arcpy

# ── Konstanter ────────────────────────────────────────────────────────────────

WGS84_WKID = 4326

# ArcGIS fälttyp (Describe.type) -> AddField field_type-nyckelord. Båda sidor
# är arcpy:s egna, otranslaterade nycklar.
FIELD_TYPE_MAP = {
    "SmallInteger": "SHORT",
    "Integer": "LONG",
    "Single": "FLOAT",
    "Double": "DOUBLE",
    "String": "TEXT",
}

SORT_NONE = "Ingen (databasens ordning)"
SORT_FIELD = "Fält"
SORT_LON = "Longitud (centroid, WGS84)"
SORT_LAT = "Latitud (centroid, WGS84)"
SORT_ROWS = "Longitud och latitud (rad för rad)"

ORDER_ASC = "Stigande"
ORDER_DESC = "Fallande"


def _sr_is_valid(sr):
    """Samma test som i övriga verktyg i denna mapp: factoryCode 0 är giltigt
    för ett eget definierat koordinatsystem så länge det har en WKT-definition."""
    if sr is None:
        return False
    try:
        if sr.factoryCode:
            return True
        return bool(sr.exportToString())
    except Exception:
        return False


def _keyed_oids(in_layer, oid_field, sort_mode, sort_field):
    """
    Sorteringsvärde per objekt för SORT_FIELD, SORT_LON och SORT_LAT.

    Returnerar (keyed, unkeyed): keyed är en lista av (oid, värde) för objekt
    som har ett värde att sortera på, unkeyed är oid för objekt som saknar ett
    (NULL i sorteringsfältet, eller ingen geometri) — dessa hamnar sist
    oavsett sorteringsordning, samma princip på fält- och geometrisortering.
    """
    if sort_mode == SORT_FIELD:
        with arcpy.da.SearchCursor(in_layer, [oid_field, sort_field]) as cur:
            rows = list(cur)
        keyed = [(o, v) for o, v in rows if v is not None]
        unkeyed = [o for o, v in rows if v is None]
        return keyed, unkeyed

    desc = arcpy.Describe(in_layer)
    source_sr = desc.spatialReference
    if not _sr_is_valid(source_sr):
        raise ValueError(
            "Lagret saknar koordinatsystem — kan inte beräkna longitud/latitud för centroiden."
        )
    wgs84_sr = arcpy.SpatialReference(WGS84_WKID)
    axis = 0 if sort_mode == SORT_LON else 1

    keyed = []
    unkeyed = []
    with arcpy.da.SearchCursor(in_layer, [oid_field, "SHAPE@"]) as cur:
        for oid, shape in cur:
            if shape is None:
                unkeyed.append(oid)
                continue
            # shape.centroid is a bare arcpy.Point with no CRS methods of its
            # own (no .projectAs) — it has to be wrapped in a PointGeometry
            # that carries the layer's spatial reference before it can be
            # reprojected to WGS84.
            centroid_geom = arcpy.PointGeometry(shape.centroid, source_sr)
            if source_sr.factoryCode != wgs84_sr.factoryCode:
                centroid_geom = centroid_geom.projectAs(wgs84_sr)
            centroid = centroid_geom.centroid
            keyed.append((oid, (centroid.X, centroid.Y)[axis]))
    return keyed, unkeyed


def _row_major_oids(in_layer, oid_field, messages):
    """
    Läsordning: rad för rad uppifrån (norr) och ned, väster till öster inom
    varje rad — som att läsa en sida.

    Antalet rader uppskattas automatiskt: N objekt spridda jämnt över en
    rektangel med bredd b och höjd h motsvarar ungefär sqrt(N * h / b) rader
    om de låg på ett regelbundet rutnät. b räknas i longitudgrader korrigerat
    med cos(medellatitud), inte i råa longitudgrader — en longitudgrad täcker
    bara cos(latitud) gånger så långt marksträcka som en latitudgrad (t.ex.
    en faktor ~0.51 vid 59° N), så utan korrigeringen ser ett rutnät mycket
    bredare ut än det är och radantalet blir fel. Latitudglappen som avgör
    de faktiska radgränserna (nedan) påverkas inte av detta, eftersom en
    latitudgrad täcker samma marksträcka oavsett longitud.

    Radgränserna sätts sedan vid de (antal rader - 1) största glappen mellan
    på varandra följande latituder, inte vid en jämnt uppdelad höjd. Ett
    regelbundet rutnät med viss spridning (jitter) inom varje rad har typiskt
    mycket mindre spridning inom en rad än avståndet mellan rader, så de
    verkliga glappen pekar ut de verkliga radgränserna. En jämnt uppdelad
    höjd gör inte det — dess skivgränser hamnar var som helst i utbredningen
    och kan lika gärna skära rakt igenom en rad som råka falla mellan två.

    Returnerar (ordered, unkeyed): ordered är oid i läsordning för objekt med
    geometri, unkeyed är oid för objekt utan geometri (hamnar sist i _run,
    samma princip som i _keyed_oids).
    """
    desc = arcpy.Describe(in_layer)
    source_sr = desc.spatialReference
    if not _sr_is_valid(source_sr):
        raise ValueError(
            "Lagret saknar koordinatsystem — kan inte beräkna longitud/latitud för centroiden."
        )
    wgs84_sr = arcpy.SpatialReference(WGS84_WKID)

    points = []
    unkeyed = []
    with arcpy.da.SearchCursor(in_layer, [oid_field, "SHAPE@"]) as cur:
        for oid, shape in cur:
            if shape is None:
                unkeyed.append(oid)
                continue
            centroid_geom = arcpy.PointGeometry(shape.centroid, source_sr)
            if source_sr.factoryCode != wgs84_sr.factoryCode:
                centroid_geom = centroid_geom.projectAs(wgs84_sr)
            centroid = centroid_geom.centroid
            points.append((oid, centroid.X, centroid.Y))

    if not points:
        return [], unkeyed

    n = len(points)
    lons = [p[1] for p in points]
    lats = [p[2] for p in points]
    width = max(lons) - min(lons)
    height = max(lats) - min(lats)

    if n == 1 or height == 0:
        n_rows = 1
    elif width == 0:
        n_rows = n
    else:
        mean_lat = sum(lats) / n
        # Longitudgrader krymper mot polerna; korrigera till samma
        # marksträcka-per-grad som latitud innan bredd/höjd jämförs. "or
        # width" skyddar bara mot exakt 90° medellatitud (poler), som inte
        # förekommer i praktiken för det här verktyget.
        width_m_equiv = width * math.cos(math.radians(mean_lat)) or width
        n_rows = max(1, min(n, round(math.sqrt(n * height / width_m_equiv))))

    messages.addMessage(
        "Radindelning: {} rad(er), uppskattat automatiskt från {} objekts "
        "utbredning.".format(n_rows, n)
    )

    # Sortera fallande på latitud (norr först) och lägg radgränserna vid de
    # största glappen i den ordningen, i stället för att dela en jämn höjd
    # i n_rows lika breda skivor — se docstring ovan.
    by_lat = sorted(points, key=lambda p: p[2], reverse=True)
    n_boundaries = n_rows - 1
    if n_boundaries > 0:
        gaps = sorted(
            range(len(by_lat) - 1),
            key=lambda i: by_lat[i][2] - by_lat[i + 1][2],
            reverse=True,
        )
        boundary_after = set(gaps[:n_boundaries])
    else:
        boundary_after = set()

    keyed = []
    row_idx = 0
    for i, (oid, lon, _lat) in enumerate(by_lat):
        keyed.append((oid, (row_idx, lon)))
        if i in boundary_after:
            row_idx += 1

    keyed.sort(key=lambda pair: pair[1])
    return [oid for oid, _ in keyed], unkeyed


# =============================================================================
# Toolbox
# =============================================================================

class Toolbox:
    def __init__(self):
        self.label = "Generera unika ID:n"
        self.alias = "generera_unika_id"
        self.tools = [GenerateUniqueIDs]


class GenerateUniqueIDs:
    def __init__(self):
        self.label = "Generera unika ID:n"
        self.description = (
            "Tilldelar sekventiella ID:n till ett fält i ett vektorlager. Skapar "
            "fältet om det inte redan finns, annars skrivs befintliga värden över.\n\n"
            "Sorteringen styr vilket objekt som får det lägsta ID:t: databasens egen "
            "ordning, ett valfritt fält, longitud eller latitud för objektets "
            "centroid, eller bådadera i kombination som läsordning ('rad för "
            "rad' — uppifrån och ned, väster till öster inom varje rad, med "
            "radhöjden uppskattad automatiskt). Centroiden omvandlas alltid till "
            "WGS84, oavsett lagrets eget koordinatsystem."
        )
        self.canRunInBackground = False

    def getParameterInfo(self):
        p_layer = arcpy.Parameter(
            displayName="Vektorlager", name="in_layer", datatype="GPFeatureLayer",
            parameterType="Required", direction="Input",
        )

        p_field_name = arcpy.Parameter(
            displayName="Namn på ID-fält", name="id_field_name", datatype="GPString",
            parameterType="Required", direction="Input",
        )

        p_field_type = arcpy.Parameter(
            displayName="Fälttyp", name="field_type", datatype="GPString",
            parameterType="Required", direction="Input",
        )
        p_field_type.filter.type = "ValueList"
        p_field_type.filter.list = ["LONG", "SHORT", "DOUBLE", "FLOAT", "TEXT"]
        p_field_type.value = "LONG"

        p_start = arcpy.Parameter(
            displayName="Startnummer", name="start_number", datatype="GPDouble",
            parameterType="Required", direction="Input",
        )
        p_start.value = 1

        p_sort_mode = arcpy.Parameter(
            displayName="Sortera efter", name="sort_mode", datatype="GPString",
            parameterType="Required", direction="Input",
        )
        p_sort_mode.filter.type = "ValueList"
        p_sort_mode.filter.list = [SORT_NONE, SORT_FIELD, SORT_LON, SORT_LAT, SORT_ROWS]
        p_sort_mode.value = SORT_NONE

        p_sort_field = arcpy.Parameter(
            displayName="Sorteringsfält", name="sort_field", datatype="Field",
            parameterType="Optional", direction="Input",
        )
        p_sort_field.parameterDependencies = [p_layer.name]
        p_sort_field.filter.list = ["Short", "Long", "Float", "Double", "Text", "Date"]
        p_sort_field.enabled = False

        p_sort_order = arcpy.Parameter(
            displayName="Sorteringsordning", name="sort_order", datatype="GPString",
            parameterType="Optional", direction="Input",
        )
        p_sort_order.filter.type = "ValueList"
        p_sort_order.filter.list = [ORDER_ASC, ORDER_DESC]
        p_sort_order.value = ORDER_ASC
        p_sort_order.enabled = False

        return [p_layer, p_field_name, p_field_type, p_start, p_sort_mode,
                p_sort_field, p_sort_order]

    def isLicensed(self):
        return True

    def updateParameters(self, parameters):
        (p_layer, p_field_name, p_field_type, _p_start, p_sort_mode,
         p_sort_field, p_sort_order) = parameters

        in_layer = p_layer.value
        field_name = p_field_name.value

        if in_layer and field_name:
            try:
                name_upper = str(field_name).strip().upper()
                field_lookup = {f.name.upper(): f for f in arcpy.ListFields(in_layer)}
                if name_upper in field_lookup:
                    mapped = FIELD_TYPE_MAP.get(field_lookup[name_upper].type)
                    if mapped:
                        p_field_type.value = mapped
                    p_field_type.enabled = False
                else:
                    p_field_type.enabled = True
            except Exception:
                p_field_type.enabled = True
        else:
            p_field_type.enabled = True

        sort_mode = p_sort_mode.value
        p_sort_field.enabled = (sort_mode == SORT_FIELD)
        if sort_mode in (SORT_FIELD, SORT_LON, SORT_LAT, SORT_ROWS):
            p_sort_order.enabled = True
        else:
            p_sort_order.enabled = False
            p_sort_order.value = ORDER_ASC

    def updateMessages(self, parameters):
        (_p_layer, _p_field_name, p_field_type, p_start, p_sort_mode,
         p_sort_field, _p_sort_order) = parameters

        if p_sort_mode.value == SORT_FIELD and not p_sort_field.value:
            p_sort_field.setErrorMessage(
                "Ange ett sorteringsfält, eller välj ett annat alternativ under "
                "'Sortera efter'."
            )

        if (p_field_type.value in ("SHORT", "LONG", "TEXT")
                and p_start.value is not None
                and p_start.value != int(p_start.value)):
            p_start.setWarningMessage(
                "Startnumret avrundas till heltal eftersom fälttypen kräver det."
            )

    def execute(self, parameters, messages):
        in_layer = parameters[0].value
        field_name = parameters[1].valueAsText.strip()
        field_type = parameters[2].valueAsText
        start_num = parameters[3].value
        sort_mode = parameters[4].valueAsText
        sort_field = parameters[5].valueAsText
        sort_order = parameters[6].valueAsText or ORDER_ASC

        try:
            _run(in_layer, field_name, field_type, start_num, sort_mode,
                 sort_field, sort_order, messages)
        except ValueError as exc:
            messages.addErrorMessage(str(exc))
            raise arcpy.ExecuteError

    def postExecute(self, parameters):
        return


# =============================================================================
# Körningens innehåll (separat funktion — går att testa utanför Pro)
# =============================================================================

def _run(in_layer, field_name, field_type, start_num, sort_mode, sort_field,
          sort_order, messages):
    existing = [f.name.upper() for f in arcpy.ListFields(in_layer)]
    if field_name.upper() not in existing:
        arcpy.management.AddField(
            in_table=in_layer, field_name=field_name, field_type=field_type,
            field_alias=field_name, field_is_nullable="NULLABLE",
        )
        messages.addMessage("Skapade fältet '{}' ({}).".format(field_name, field_type))
    else:
        messages.addMessage(
            "Fältet '{}' finns redan - befintliga värden skrivs över.".format(field_name)
        )

    oid_field = arcpy.Describe(in_layer).OIDFieldName
    reverse = (sort_order == ORDER_DESC)

    if sort_mode == SORT_NONE:
        with arcpy.da.SearchCursor(in_layer, [oid_field]) as cur:
            ordered_oids = [row[0] for row in cur]
    elif sort_mode == SORT_ROWS:
        messages.addMessage(
            "Sorterar rad för rad (uppifrån och ned, väster till öster) ({}).".format(sort_order)
        )
        ordered, unkeyed = _row_major_oids(in_layer, oid_field, messages)
        if reverse:
            ordered = list(reversed(ordered))
        ordered_oids = ordered + unkeyed
    else:
        if sort_mode == SORT_FIELD:
            messages.addMessage("Sorterar efter '{}' ({}).".format(sort_field, sort_order))
        else:
            axis_name = "longitud" if sort_mode == SORT_LON else "latitud"
            messages.addMessage(
                "Sorterar efter centroidens {} ({}).".format(axis_name, sort_order)
            )
        keyed, unkeyed = _keyed_oids(in_layer, oid_field, sort_mode, sort_field)
        keyed.sort(key=lambda pair: pair[1], reverse=reverse)
        ordered_oids = [oid for oid, _ in keyed] + unkeyed

    if not ordered_oids:
        messages.addWarningMessage("Indatalagret har inga objekt. Inget att göra.")
        return

    messages.addMessage(
        "Tilldelar {} ID:n från och med {}.".format(len(ordered_oids), start_num)
    )

    if field_type in ("SHORT", "LONG", "TEXT"):
        start = int(start_num)
    else:
        start = float(start_num)

    if field_type == "TEXT":
        id_map = {oid: str(start + i) for i, oid in enumerate(ordered_oids)}
    else:
        id_map = {oid: start + i for i, oid in enumerate(ordered_oids)}

    catalog_path = arcpy.Describe(in_layer).catalogPath
    workspace = arcpy.Describe(catalog_path).path
    while arcpy.Describe(workspace).dataType == "FeatureDataset":
        workspace = arcpy.Describe(workspace).path
    is_gdb = arcpy.Describe(workspace).workspaceType in ("LocalDatabase", "RemoteDatabase")

    def _apply_ids():
        with arcpy.da.UpdateCursor(catalog_path, [oid_field, field_name]) as cur:
            for row in cur:
                if row[0] in id_map:
                    row[1] = id_map[row[0]]
                    cur.updateRow(row)

    if is_gdb:
        editor = arcpy.da.Editor(workspace)
        editor.startEditing(False, False)
        try:
            _apply_ids()
        except Exception:
            editor.stopEditing(False)
            raise
        editor.stopEditing(True)
    else:
        _apply_ids()

    messages.addMessage("Klart. {} objekt uppdaterade.".format(len(ordered_oids)))
