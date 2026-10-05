import { useEffect, useRef, useState } from "react";
import * as maplibregl from "maplibre-gl";
import type { GeoJSONSource } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import workerUrl from "maplibre-gl/dist/maplibre-gl-worker.mjs?worker&url";
import { Expand, MapPin } from "lucide-react";
import { Button } from "@/components/ui/button";
import { STATIONS, VENUE, MAP_STYLE, type LngLat } from "@/data/geography";

// MapLibre 6 needs its worker bundled separately by Vite, including shared imports.
maplibregl.setWorkerUrl(workerUrl);

interface Props {
  selected: string;
  onSelect: (id: string) => void;
  minute: number;
  density: number;
  opacity: number;
  labels: boolean;
}
const bounds: [[number, number], [number, number]] = [
  [121.5038, 25.048],
  [121.5225, 25.065],
];
/** Equal-distance movement along a schematic route, not a calibrated trajectory. */
function along(points: LngLat[], t: number): LngLat {
  const lengths = points
    .slice(1)
    .map((p, i) =>
      Math.hypot(
        (p[0] - points[i][0]) * Math.cos((25.05 * Math.PI) / 180),
        p[1] - points[i][1],
      ),
    );
  let remaining = lengths.reduce((a, b) => a + b, 0) * t;
  for (let i = 0; i < lengths.length; i++) {
    if (remaining <= lengths[i] || i === lengths.length - 1) {
      const f = lengths[i] ? remaining / lengths[i] : 0;
      return [
        points[i][0] + (points[i + 1][0] - points[i][0]) * f,
        points[i][1] + (points[i + 1][1] - points[i][1]) * f,
      ];
    }
    remaining -= lengths[i];
  }
  return points[0];
}
export function FlowMap(props: Props) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<maplibregl.Map | null>(null);
  const markers = useRef<maplibregl.Marker[]>([]);
  const latest = useRef(props);
  const [ready, setReady] = useState(false);
  const [fallback, setFallback] = useState(false);
  const [tileError, setTileError] = useState(false);
  useEffect(() => {
    latest.current = props;
  }, [props]);
  useEffect(() => {
    setReady(false);
    if (fallback || !container.current) return;
    let instance: maplibregl.Map;
    try {
      instance = new maplibregl.Map({
        container: container.current,
        style: MAP_STYLE,
        center: [121.512, 25.0567],
        zoom: 14.4,
        attributionControl: { compact: true },
        maxZoom: 17,
        minZoom: 12,
        renderWorldCopies: false,
        dragRotate: false,
        pitchWithRotate: false,
      });
    } catch {
      setFallback(true);
      return;
    }
    map.current = instance;
    let disposed = false;
    const timeout = window.setTimeout(() => {
      if (!instance.isStyleLoaded()) setTileError(true);
    }, 14000);
    instance.on("error", () => {
      if (!disposed) setTileError(true);
    });
    instance.on("load", () => {
      if (disposed) return;
      window.clearTimeout(timeout);
      setTileError(false);
      instance.fitBounds(bounds, {
        padding: { top: 55, bottom: 55, left: 65, right: 80 },
        duration: 0,
      });
      instance.addSource("routes", {
        type: "geojson",
        data: {
          type: "FeatureCollection",
          features: STATIONS.map((s) => ({
            type: "Feature",
            properties: { station: s.id, color: s.color },
            geometry: { type: "LineString", coordinates: s.route },
          })),
        },
      });
      instance.addLayer({
        id: "route-glow",
        type: "line",
        source: "routes",
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": ["get", "color"],
          "line-width": 12,
          "line-opacity": 0.07,
          "line-blur": 4,
        },
      });
      instance.addLayer({
        id: "route-lines",
        type: "line",
        source: "routes",
        layout: { "line-cap": "round", "line-join": "round" },
        paint: {
          "line-color": ["get", "color"],
          "line-width": [
            "case",
            ["==", ["get", "station"], latest.current.selected],
            3,
            1.5,
          ],
          "line-opacity": latest.current.opacity,
        },
      });
      instance.addLayer({
        id: "route-hit",
        type: "line",
        source: "routes",
        paint: { "line-width": 18, "line-opacity": 0 },
      });
      instance.addSource("particles", {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
      });
      instance.addLayer({
        id: "particles",
        type: "circle",
        source: "particles",
        paint: {
          "circle-radius": [
            "case",
            ["==", ["get", "station"], latest.current.selected],
            2.9,
            1.8,
          ],
          "circle-color": ["get", "color"],
          "circle-opacity": 0.95,
          "circle-stroke-width": 2,
          "circle-stroke-color": "#ffffff",
          "circle-stroke-opacity": 0.08,
        },
      });
      for (const station of STATIONS) {
        const element = document.createElement("button");
        element.className = "map-station-marker";
        element.type = "button";
        element.setAttribute("aria-label", `選擇${station.name}站`);
        const dot = document.createElement("span");
        dot.className = "marker-dot";
        const label = document.createElement("span");
        label.className = "marker-label";
        label.textContent = station.name;
        element.append(dot, label);
        element.addEventListener("click", (event) => {
          // A marker click must not also select a route underneath its label.
          event.stopPropagation();
          latest.current.onSelect(station.id);
        });
        markers.current.push(
          new maplibregl.Marker({ element, anchor: "left", offset: [-6, 0] })
            .setLngLat(station.coordinates)
            .addTo(instance),
        );
      }
      const origin = document.createElement("div");
      origin.className = "venue-marker";
      origin.innerHTML =
        '<span class="venue-rings"><i></i></span><span>大稻埕活動區<small>活動代表位置</small></span>';
      markers.current.push(
        new maplibregl.Marker({
          element: origin,
          anchor: "left",
          offset: [-14, 0],
        })
          .setLngLat(VENUE.coordinates)
          .addTo(instance),
      );
      instance.on("click", "route-hit", (e) => {
        const id = e.features?.[0]?.properties?.station;
        if (typeof id === "string") latest.current.onSelect(id);
      });
      instance.on("mouseenter", "route-hit", () => {
        instance.getCanvas().style.cursor = "pointer";
      });
      instance.on("mouseleave", "route-hit", () => {
        instance.getCanvas().style.cursor = "";
      });
      setReady(true);
    });
    const resize = new ResizeObserver(() => instance.resize());
    resize.observe(container.current);
    return () => {
      disposed = true;
      window.clearTimeout(timeout);
      resize.disconnect();
      markers.current.forEach((m) => m.remove());
      markers.current = [];
      instance.remove();
      map.current = null;
    };
  }, [fallback]);
  useEffect(() => {
    const instance = map.current;
    if (!ready || !instance || !instance.getSource("particles")) return;
    instance.setPaintProperty("route-lines", "line-opacity", props.opacity);
    instance.setPaintProperty("route-lines", "line-width", [
      "case",
      ["==", ["get", "station"], props.selected],
      3.2,
      1.3,
    ]);
    instance.setPaintProperty("particles", "circle-radius", [
      "case",
      ["==", ["get", "station"], props.selected],
      2.9,
      1.6,
    ]);
    const features = STATIONS.flatMap((s) =>
      Array.from({ length: props.density }, (_, i) => ({
        type: "Feature" as const,
        properties: { color: s.color, station: s.id },
        geometry: {
          type: "Point" as const,
          coordinates: along(
            s.route,
            (i / props.density + props.minute / 45) % 1,
          ),
        },
      })),
    );
    (instance.getSource("particles") as GeoJSONSource).setData({
      type: "FeatureCollection",
      features,
    });
    markers.current.slice(0, STATIONS.length).forEach((m, i) => {
      const element = m.getElement();
      element.classList.toggle(
        "is-selected",
        STATIONS[i].id === props.selected,
      );
      element.classList.toggle("hide-label", !props.labels);
    });
  }, [
    ready,
    props.minute,
    props.density,
    props.opacity,
    props.labels,
    props.selected,
  ]);
  return (
    <div className="map-stage">
      {!fallback && <div ref={container} className="map-canvas" />}
      {fallback && <SchematicMap {...props} />}
      <div className="map-caption">
        <span className="map-caption-dot" />
        地理底圖 × 示意路徑
      </div>
      <Button
        variant="outline"
        size="icon"
        className="map-fit"
        aria-label="回到完整地圖"
        onClick={() =>
          map.current?.fitBounds(bounds, { padding: 60, duration: 700 })
        }
      >
        <Expand size={16} />
      </Button>
      {!ready && !fallback && !tileError && (
        <div className="map-loading">正在載入周邊地圖…</div>
      )}
      {tileError && (
        <div className="tile-error">
          底圖暫時無法載入。
          <button
            onClick={() => {
              setFallback(true);
              setTileError(false);
            }}
          >
            切換示意底圖
          </button>
        </div>
      )}
      <div className="map-legend">
        <MapPin size={13} />
        <span>活動區 → 車站</span>
        <i />
        <span>粒子不代表實際人數或足跡</span>
      </div>
    </div>
  );
}
function SchematicMap(props: Props) {
  const point = ([lng, lat]: LngLat) => [
    ((lng - bounds[0][0]) / (bounds[1][0] - bounds[0][0])) * 1000,
    650 - ((lat - bounds[0][1]) / (bounds[1][1] - bounds[0][1])) * 650,
  ];
  const start = point(VENUE.coordinates);
  return (
    <svg
      className="schematic-map"
      viewBox="0 0 1000 650"
      role="img"
      aria-label="底圖不可用時的地理位置示意圖"
    >
      <defs>
        <pattern id="grid" width="40" height="40" patternUnits="userSpaceOnUse">
          <path d="M40 0H0V40" fill="none" stroke="#252628" strokeWidth=".5" />
        </pattern>
      </defs>
      <rect width="1000" height="650" fill="#141516" />
      <rect width="1000" height="650" fill="url(#grid)" />
      <text x="28" y="610" fill="#81858a" fontSize="14">
        示意底圖 · 座標位置，非步行導航
      </text>
      {STATIONS.map((s) => (
        <g key={s.id}>
          <polyline
            points={s.route.map((p) => point(p).join(",")).join(" ")}
            fill="none"
            stroke={s.color}
            strokeWidth={s.id === props.selected ? 3 : 1.4}
            opacity={props.opacity}
          />
          {Array.from({ length: props.density }, (_, i) => {
            const p = point(
              along(s.route, (i / props.density + props.minute / 45) % 1),
            );
            return <circle key={i} cx={p[0]} cy={p[1]} r="2" fill={s.color} />;
          })}
          <g
            onClick={() => props.onSelect(s.id)}
            tabIndex={0}
            role="button"
            aria-label={`選擇${s.name}站`}
            onKeyDown={(e) => {
              if (e.key === "Enter" || e.key === " ") props.onSelect(s.id);
            }}
          >
            <circle
              cx={point(s.coordinates)[0]}
              cy={point(s.coordinates)[1]}
              r="7"
              fill={s.color}
            />
            {props.labels && (
              <text
                x={point(s.coordinates)[0] + 14}
                y={point(s.coordinates)[1] + 5}
                fill="#f5f5f4"
                fontSize="17"
              >
                {s.name}
              </text>
            )}
          </g>
        </g>
      ))}
      <circle cx={start[0]} cy={start[1]} r="10" fill="#eee" />
      <text x={start[0] - 20} y={start[1] - 24} fill="#f5f5f4" fontSize="17">
        大稻埕活動區
      </text>
    </svg>
  );
}
