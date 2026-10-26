# Railway station data

`stations.json` is a filtered derivative of [`stations.json`](https://github.com/prasenjit-27/Indian-Railway-Data/blob/1dc0ddbc6a06b09ef69d4825ff3127c1f29640fa/stations.json) and `trains.json` from [Indian-Railway-Data](https://github.com/prasenjit-27/Indian-Railway-Data/tree/1dc0ddbc6a06b09ef69d4825ff3127c1f29640fa), commit `1dc0ddbc6a06b09ef69d4825ff3127c1f29640fa`. The source repository releases its data under the MIT License; see [LICENSE](./LICENSE).

The station table keeps valid Indian station codes and coordinates for stations appearing on at least one active train schedule. To avoid selecting minor halts, it then retains stations whose names identify them as a junction, central, or terminal station, or which appear as an origin or destination for at least seven distinct active trains in the source schedules. Placeholder codes and coordinates outside India are excluded.
