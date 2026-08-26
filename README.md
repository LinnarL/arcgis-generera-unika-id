# Generate unique numeric IDs

ArcGIS Pro Python toolbox that assigns sequential IDs to a field in a vector layer. The field
is created if it does not already exist; if it does, its values are overwritten.

Sorting decides which feature gets the lowest ID: the geodatabase's own row order, a chosen
field, the longitude or latitude of each feature's centroid, or both combined as reading order
(top to bottom, left to right within each row). Centroids are always reprojected to WGS84
before longitude/latitude is read, so that sort order does not depend on the layer's own
coordinate system.

## Requirements

- ArcGIS Pro 3.x. Developed and tested on 3.6 with Python 3.13.
- No dependencies beyond `arcpy` and the standard library.

## Install

1. Clone or download this repo.
2. In ArcGIS Pro: Catalog, Toolboxes, Add Toolbox, select `GenerateUniqueIDs.pyt`.
3. Open Generera unika ID:n, Generera unika ID:n.

## The tool dialog

The UI is in Swedish, matching a Swedish ArcGIS Pro install.

| Parameter | Default | Notes |
|---|---|---|
| Vektorlager | - | Input feature layer |
| Namn på ID-fält | - | Created if missing. If it already exists, its field type is read and locked in below |
| Fälttyp | LONG | `LONG`, `SHORT`, `DOUBLE`, `FLOAT` or `TEXT`. Locked to the existing type when the field already exists |
| Startnummer | 1 | First ID assigned to the lowest-sorted feature |
| Sortera efter | Ingen (databasens ordning) | `Ingen`, `Fält`, centroid `Longitud`/`Latitud` (WGS84), or `Longitud och latitud` (row by row) |
| Sorteringsfält | - | Shown only when "Sortera efter" is `Fält` |
| Sorteringsordning | Stigande | `Stigande` (ascending) or `Fallande` (descending) |

Features with a NULL value in the sort field, or no geometry, are always placed last regardless
of sort order.

## Output

IDs are written in place to the chosen field on the input layer, in an edit session when the
data lives in a geodatabase. `TEXT` fields get the ID formatted as a plain string; numeric field
types get the ID as a number, truncated to an integer for `SHORT`/`LONG` if the starting number
was not a whole number.

## Notes

- Sorting by longitude or latitude uses each feature's centroid, reprojected to WGS84
  (EPSG:4326), not the layer's native coordinates — this keeps the sort order meaningful and
  reproducible regardless of the project's coordinate system.
- "Longitud och latitud (rad för rad)" gives reading order: top row first, west to east within
  each row. The number of rows is estimated automatically from the feature count and the
  centroids' bounding box, correcting the box's width for the fact that a degree of longitude
  covers less ground than a degree of latitude away from the equator (a factor of `cos(latitude)`
  — about 0.51 at 59°N). Row boundaries are then placed at the largest gaps between consecutive
  latitudes rather than at evenly spaced cutoffs, so a regular survey grid with some per-point
  jitter still separates cleanly into rows even though the jitter is not evenly distributed
  across the extent. Works best when within-row scatter is clearly smaller than the spacing
  between rows; very irregularly scattered points will still get a full, deterministic order,
  just not necessarily one that looks like clean rows.
- `arcpy`'s field type keywords (`SHORT`, `LONG`, `DOUBLE`, `FLOAT`, `TEXT`) are used as-is in
  the dialog; they are not translated since they are literal `AddField` parameter values.
