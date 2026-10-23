# Data attribution

## OpenStreetMap (street network and facilities)

© OpenStreetMap contributors. The data is available under the
[Open Database License (ODbL) 1.0](https://opendatacommons.org/licenses/odbl/1-0/); see
<https://www.openstreetmap.org/copyright>.

`data/osm/` is a derived database built from OpenStreetMap: the walking network (osmnx `walk`
filter) and the `amenity=hospital|clinic|doctors` features inside a bounding box around central
Bengaluru, reprojected to EPSG:32643. The committed extract is a derived database and stays under
the ODbL; anyone who redistributes it or a database built from it must attribute OpenStreetMap
contributors and keep it under the same licence.

- Source: Overpass API (`https://overpass-api.de/api`) through osmnx 2.1.1.
- Fetched: 2026-10-23. Overpass data timestamps (`osm_base`): 2026-10-23T16:45:51Z (network) and
  2026-10-23T16:53:51Z (facilities). Recorded in `data/osm/meta.json`.

## WorldPop (population)

- Product: WorldPop Global2 R2025A v1, "Individual countries 2015-2030 (100 m resolution)",
  constrained estimates of the number of people per grid square, India, year 2025.
- File: `ind_pop_2025_CN_100m_R2025A_v1.tif` (778,106,191 bytes, 3 arc-second grid, EPSG:4326)
- URL: <https://data.worldpop.org/GIS/Population/Global_2015_2030/R2025A/2025/IND/v1/100m/constrained/ind_pop_2025_CN_100m_R2025A_v1.tif>
- Licence: [Creative Commons Attribution 4.0 International (CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/)
- SHA-256 of the raw file: `f5717c622d79052d4aacf0f67365165575855ba8059375b5d87ea655ed26fa53`
- Downloaded: 2026-10-23 (the raster is not committed).
- Citation requested by WorldPop: Bondarenko M., Priyatikanto R., Tejedor-Garavito N., Zhang W.,
  McKeen T., Cunningham A., Woods T., Hilton J., Cihan D., Nosatiuk B., Brinkhoff T., Tatem A.,
  Sorichetta A. Constrained estimates of 2015-2030 total number of people per grid square at a
  resolution of 3 arc (approximately 100m at the equator) R2025A version v1. Global Demographic
  Data Project, funded by The Bill and Melinda Gates Foundation (INV-045237). WorldPop, School of
  Geography and Environmental Science, University of Southampton.
  DOI: 10.5258/SOTON/WP00839
- Release listing: <https://hub.worldpop.org/geodata/listing?id=135>. WorldPop describes R2025A as
  an alpha release that may change.

`data/pop_h3.csv` (H3 resolution 9) and `data/pop_h3_r10.csv` (resolution 10) sum the raster's
pixel values by pixel centre inside the study area; they are derived from this product and carry
the same CC BY 4.0 attribution.
