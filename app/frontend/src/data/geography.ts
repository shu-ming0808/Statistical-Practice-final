export type LngLat = [number, number];

export interface MapStation {
  /** Exact station label in the existing analysis database. */
  id: string;
  name: string;
  label: string;
  coordinates: LngLat;
  color: string;
  route: LngLat[];
  routeDescription: string;
}

/** Tourism Administration's representative event location, not a launch barge. */
export const VENUE: { name: string; coordinates: LngLat } = {
  name: "大稻埕河濱活動區",
  coordinates: [121.5063, 25.0555],
};

// These hand-picked, simplified lines illustrate movement via nearby roads.
// They are not observed trajectories, turn-by-turn directions, validated crowd
// evacuation routes, or proof that any particular floodgate was open on a date.
// Road alignment references OpenStreetMap; representative entrance coordinates
// come from Taipei Metro's official public CSV (retrieved 2026-10-04).
const WATERFRONT: LngLat[] = [
  VENUE.coordinates,
  [121.50705, 25.0565],
  [121.5075, 25.05682],
  [121.508188, 25.056882],
  [121.508933, 25.05688],
  [121.509838, 25.056905],
];

const YANPING_NORTH: LngLat[] = [
  ...WATERFRONT,
  [121.511582, 25.056941],
  [121.511455, 25.058998],
  [121.511353, 25.060652],
  [121.511253, 25.062257],
  [121.51122, 25.062804],
  [121.511212, 25.062937],
];

export const STATIONS: MapStation[] = [
  {
    id: "北門",
    name: "北門",
    label: "G13 · 出口 3",
    coordinates: [121.510227, 25.049943],
    color: "#f5f5f4",
    routeDescription: "河濱活動區 → 民生西路 → 迪化街 → 塔城街",
    route: [
      ...WATERFRONT,
      [121.509997, 25.055647],
      [121.510077, 25.055034],
      [121.510149, 25.054163],
      [121.510257, 25.053766],
      [121.51033, 25.053255],
      [121.510417, 25.052481],
      [121.510474, 25.052036],
      [121.510517, 25.05138],
      [121.51052, 25.050636],
      [121.5105, 25.050067],
      [121.510227, 25.049943],
    ],
  },
  {
    id: "大橋頭站",
    name: "大橋頭",
    label: "O12 · 出口 1A",
    coordinates: [121.5117348, 25.06333204],
    color: "#b9b6b1",
    routeDescription: "河濱活動區 → 民生西路 → 延平北路 → 民權西路",
    route: [
      ...YANPING_NORTH,
      [121.511371, 25.063041],
      [121.511812, 25.063044],
      [121.5117348, 25.06333204],
    ],
  },
  {
    id: "雙連",
    name: "雙連",
    label: "R12 · 出口 1",
    coordinates: [121.520588, 25.057499],
    color: "#92979e",
    routeDescription: "河濱活動區 → 民生西路 → 雙連站",
    route: [
      ...WATERFRONT,
      [121.511582, 25.056941],
      [121.513694, 25.056991],
      [121.515732, 25.057008],
      [121.517486, 25.057251],
      [121.51919, 25.057464],
      [121.520246, 25.057596],
      [121.520527, 25.057631],
      [121.520588, 25.057499],
    ],
  },
  {
    id: "民權西路",
    name: "民權西路",
    label: "R13 / O11 · 出口 6",
    coordinates: [121.5188804, 25.06273597],
    color: "#77736d",
    routeDescription: "河濱活動區 → 延平北路 → 民權西路",
    route: [
      ...YANPING_NORTH,
      [121.51122, 25.062804],
      [121.512563, 25.062858],
      [121.513687, 25.06291],
      [121.514702, 25.062889],
      [121.515963, 25.062882],
      [121.516884, 25.062843],
      [121.518217, 25.062827],
      [121.5188, 25.062811],
      [121.5188804, 25.06273597],
    ],
  },
];

export const MAP_STYLE = "https://tiles.openfreemap.org/styles/dark";

export const SOURCE_LINKS = [
  {
    label: "臺北捷運出入口座標",
    url: "https://data.taipei/dataset/detail?id=cfa4778c-62c1-497b-b704-756231de348b",
  },
  {
    label: "交通部觀光署活動代表位置",
    url: "https://media.taiwan.net.tw/en-us/portal/travel/details/event_a15010000h_081668",
  },
  {
    label: "© OpenStreetMap contributors",
    url: "https://www.openstreetmap.org/copyright",
  },
  {
    label: "OpenFreeMap",
    url: "https://openfreemap.org/",
  },
];
