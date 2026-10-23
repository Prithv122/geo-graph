# Spot-check sheet

20 cells drawn at random (fixed seed) from the registered access result. For each row:

1. Open the link. It asks the openstreetmap.org foot router for the walk from the cell centroid to the facility the network analysis chose.
2. Read the walking distance the router shows, in metres, and type it into `router_m` in `spot_check.csv`. Leave `within_15pct` blank; scoring fills it in.
3. Do not adjust the link's endpoints: the point of the check is that the router never sees the analysis network.

Pass rule (pre-registered, Amendment 1): a row passes when our network distance and the router's differ by at most 15% **or** at most 100 m (the router joins the street differently, which matters on short routes). The facility is the one the network analysis chose. The sheet passes with at least 16 of 20 rows.

If fewer than 16 of 20 pass, the verdict is still reported but marked "not validated by the spot-check", and every miss is investigated and written up in `NOTES.md` before the README reports the result.

| Cell | Facility | Network distance | Router | router_m |
|---|---|---|---|---|
| `8960145b0d7ffff` | `node/6938690598` | 516 m | [route](https://www.openstreetmap.org/directions?engine=fossgis_osrm_foot&route=12.990285%2C77.581569%3B12.987810%2C77.580983) |  |
| `8960145b147ffff` | `node/12676261338` | 161 m | [route](https://www.openstreetmap.org/directions?engine=fossgis_osrm_foot&route=12.992510%2C77.553929%3B12.993725%2C77.553972) |  |
| `8960145b21bffff` | `node/4849107926` | 1,272 m | [route](https://www.openstreetmap.org/directions?engine=fossgis_osrm_foot&route=13.017004%2C77.581415%3B13.008000%2C77.583748) |  |
| `8960145b363ffff` | `node/5174994725` | 642 m | [route](https://www.openstreetmap.org/directions?engine=fossgis_osrm_foot&route=13.016022%2C77.553119%3B13.011845%2C77.552606) |  |
| `8960145b467ffff` | `node/1006462338` | 469 m | [route](https://www.openstreetmap.org/directions?engine=fossgis_osrm_foot&route=12.984935%2C77.583284%3B12.985247%2C77.580385) |  |
| `8960145b473ffff` | `way/371602983` | 734 m | [route](https://www.openstreetmap.org/directions?engine=fossgis_osrm_foot&route=12.983848%2C77.588682%3B12.988213%2C77.592159) |  |
| `8960145b527ffff` | `node/8187986393` | 728 m | [route](https://www.openstreetmap.org/directions?engine=fossgis_osrm_foot&route=12.970057%2C77.557766%3B12.971773%2C77.551965) |  |
| `8960145b68fffff` | `node/5174903787` | 590 m | [route](https://www.openstreetmap.org/directions?engine=fossgis_osrm_foot&route=12.995579%2C77.596703%3B12.991842%2C77.596853) |  |
| `89618925917ffff` | `node/4578753189` | 161 m | [route](https://www.openstreetmap.org/directions?engine=fossgis_osrm_foot&route=12.930434%2C77.580565%3B12.930688%2C77.579454) |  |
| `8961892591bffff` | `node/4447001526` | 130 m | [route](https://www.openstreetmap.org/directions?engine=fossgis_osrm_foot&route=12.934696%2C77.584246%3B12.934168%2C77.583764) |  |
| `89618925a2fffff` | `node/3862550432` | 203 m | [route](https://www.openstreetmap.org/directions?engine=fossgis_osrm_foot&route=12.965652%2C77.596199%3B12.964727%2C77.596470) |  |
| `89618925e8bffff` | `node/1468540733` | 714 m | [route](https://www.openstreetmap.org/directions?engine=fossgis_osrm_foot&route=12.944157%2C77.628339%3B12.940237%2C77.625340) |  |
| `8961892e89bffff` | `way/164048206` | 662 m | [route](https://www.openstreetmap.org/directions?engine=fossgis_osrm_foot&route=12.986881%2C77.639900%3B12.985969%2C77.643023) |  |
| `8961892e97bffff` | `node/1469181813` | 203 m | [route](https://www.openstreetmap.org/directions?engine=fossgis_osrm_foot&route=12.994465%2C77.610528%3B12.995625%2C77.611327) |  |
| `8961892e997ffff` | `node/4844863387` | 512 m | [route](https://www.openstreetmap.org/directions?engine=fossgis_osrm_foot&route=12.975208%2C77.615019%3B12.979127%2C77.615445) |  |
| `8961892eb63ffff` | `node/6939074293` | 757 m | [route](https://www.openstreetmap.org/directions?engine=fossgis_osrm_foot&route=13.015838%2C77.612093%3B13.021819%2C77.613084) |  |
| `8961892ebb3ffff` | `node/11853321328` | 249 m | [route](https://www.openstreetmap.org/directions?engine=fossgis_osrm_foot&route=12.998728%2C77.614212%3B12.997168%2C77.614588) |  |
| `8961892ebc3ffff` | `node/13647330503` | 211 m | [route](https://www.openstreetmap.org/directions?engine=fossgis_osrm_foot&route=13.008314%2C77.624609%3B13.008766%2C77.622868) |  |
| `8961892ebc7ffff` | `node/6945031202` | 151 m | [route](https://www.openstreetmap.org/directions?engine=fossgis_osrm_foot&route=13.007255%2C77.621580%3B13.008017%2C77.622148) |  |
| `8961892ed0fffff` | `node/7572526809` | 186 m | [route](https://www.openstreetmap.org/directions?engine=fossgis_osrm_foot&route=12.973032%2C77.625818%3B12.972847%2C77.626850) |  |
