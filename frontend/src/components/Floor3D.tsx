import { Edges, Html, OrbitControls } from "@react-three/drei";
import { Canvas, useFrame, useThree } from "@react-three/fiber";
import { useEffect, useMemo, useRef, useState } from "react";
import * as THREE from "three";
import { cellKey, maxMetric, rackColor, type ColorMode, type RackLink } from "../lib/floor";
import { STATUS_COLOR } from "../lib/format";
import type { FloorCell, FloorPlan } from "../lib/types";
import { useTheme } from "../lib/user";

const CELL = 1;
const RACK_H = 2.0;

/** Resolve CSS colour expressions (vars, color-mix) to something three.js understands. */
function useCssColors(exprs: string[], themeKey: string): Map<string, string> {
  return useMemo(() => {
    const probe = document.createElement("span");
    probe.style.display = "none";
    document.body.appendChild(probe);
    const out = new Map<string, string>();
    for (const e of exprs) {
      probe.style.color = "";
      probe.style.color = e;
      const resolved = getComputedStyle(probe).color;
      out.set(e, toThreeColor(resolved));
    }
    probe.remove();
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [exprs.join("|"), themeKey]);
}

function toThreeColor(css: string): string {
  // getComputedStyle may return "color(srgb r g b / a)" for color-mix results
  const m = /color\(srgb ([\d.]+) ([\d.]+) ([\d.]+)/.exec(css);
  if (m) {
    const c = new THREE.Color(Number(m[1]), Number(m[2]), Number(m[3]));
    return `#${c.getHexString()}`;
  }
  const rgb = /rgba?\(([\d.]+),\s*([\d.]+),\s*([\d.]+)/.exec(css);
  if (rgb) return `rgb(${Math.round(Number(rgb[1]))}, ${Math.round(Number(rgb[2]))}, ${Math.round(Number(rgb[3]))})`;
  return css || "#888888";
}

type Placed = { cell: FloorCell; x: number; z: number; w: number; d: number; link?: RackLink };

function layout(plan: FloorPlan): { items: Placed[]; width: number; depth: number } {
  const ri = new Map(plan.rows.map((r, i) => [r, i]));
  const ci = new Map(plan.columns.map((c, i) => [c, i]));
  const links = new Map(plan.racks.map((r) => [cellKey(r.row, r.col), r]));
  const items: Placed[] = [];
  for (const cell of plan.cells) {
    const r = ri.get(cell.row);
    const c = ci.get(cell.col);
    if (r === undefined || c === undefined) continue;
    items.push({
      cell,
      x: (c + cell.span_cols / 2) * CELL,
      z: (r + cell.span_rows / 2) * CELL,
      w: cell.span_cols * CELL,
      d: cell.span_rows * CELL,
      link: links.get(cellKey(cell.row, cell.col)),
    });
  }
  return { items, width: plan.columns.length * CELL, depth: plan.rows.length * CELL };
}

function CameraRig({ view, width, depth }: { view: "perspective" | "top"; width: number; depth: number }) {
  const { camera, controls } = useThree() as unknown as { camera: THREE.PerspectiveCamera; controls: { target: THREE.Vector3; update: () => void } | null };
  const target = useRef(new THREE.Vector3());
  const animating = useRef(false);
  const first = useRef(true);
  const size = Math.max(width, depth);
  useEffect(() => {
    const cx = width / 2;
    const cz = depth / 2;
    if (view === "top") target.current.set(cx, size * 1.25, cz + 0.001);
    else target.current.set(cx + size * 0.35, size * 0.75, cz + size * 0.95);
    if (controls) controls.target.set(cx, 0, cz);
    // the initial camera already sits at the perspective preset
    animating.current = !first.current || view === "top";
    first.current = false;
  }, [view, width, depth, size, controls]);
  useFrame(() => {
    if (!animating.current) return;
    camera.position.lerp(target.current, 0.14);
    controls?.update();
    if (camera.position.distanceTo(target.current) < 0.03) animating.current = false;
  });
  return null;
}

function RackMesh({
  item,
  color,
  colors,
  selected,
  hovered,
  onHover,
  onSelect,
  showLabel,
}: {
  item: Placed;
  color: string;
  colors: Map<string, string>;
  selected: boolean;
  hovered: boolean;
  onHover: (i: Placed | null) => void;
  onSelect: (i: Placed) => void;
  showLabel: boolean;
}) {
  const empty = item.cell.type === "empty_rack";
  const w = item.w * 0.82;
  const d = item.d * 0.88;
  const height = item.link?.rack?.height ?? 42;
  const uh = (RACK_H - 0.12) / height;
  const lift = hovered || selected ? 0.04 : 0;
  return (
    <group position={[item.x, 0, item.z]}>
      <mesh
        position={[0, RACK_H / 2 + lift, 0]}
        castShadow
        receiveShadow
        onPointerOver={(e) => {
          e.stopPropagation();
          onHover(item);
          document.body.style.cursor = "pointer";
        }}
        onPointerOut={() => {
          onHover(null);
          document.body.style.cursor = "";
        }}
        onClick={(e) => {
          e.stopPropagation();
          onSelect(item);
        }}
      >
        <boxGeometry args={[w, RACK_H, d]} />
        <meshStandardMaterial
          color={empty ? colors.get("var(--panel-3)") : color}
          transparent={empty}
          opacity={empty ? 0.18 : 1}
          roughness={0.55}
          metalness={0.15}
          emissive={selected ? colors.get("var(--accent)") : hovered ? color : "#000000"}
          emissiveIntensity={selected ? 0.35 : hovered ? 0.25 : 0}
        />
        <Edges color={selected ? colors.get("var(--accent)") : colors.get("var(--line-strong)")} threshold={15} />
      </mesh>
      {/* device slabs on the front face */}
      {!empty &&
        item.link?.devices
          .filter((dv) => dv.position)
          .map((dv) => (
            <mesh key={dv.id} position={[0, 0.06 + (dv.position! - 1) * uh + (dv.u_height * uh) / 2 + lift, d / 2 + 0.006]}>
              <boxGeometry args={[w * 0.86, Math.max(dv.u_height * uh - 0.006, 0.01), 0.012]} />
              <meshStandardMaterial color={colors.get(STATUS_COLOR[dv.status])} emissive={colors.get(STATUS_COLOR[dv.status])} emissiveIntensity={0.35} />
            </mesh>
          ))}
      {showLabel && item.link?.rack && (
        <Html position={[0, RACK_H + 0.25, 0]} center distanceFactor={10} zIndexRange={[10, 0]} style={{ pointerEvents: "none" }}>
          <div className="whitespace-nowrap rounded bg-panel/90 px-1 font-mono text-[10px] text-ink shadow">{item.link.rack.name}</div>
        </Html>
      )}
    </group>
  );
}

function StaticMesh({ item, colors }: { item: Placed; colors: Map<string, string> }) {
  const t = item.cell.type;
  if (t === "bench")
    return (
      <mesh position={[item.x, 0.37, item.z]} castShadow receiveShadow>
        <boxGeometry args={[item.w * 0.94, 0.06, item.d * 0.7]} />
        <meshStandardMaterial color={colors.get("color-mix(in srgb, var(--info) 45%, var(--panel-3))")} roughness={0.8} />
        <mesh position={[0, -0.2, 0]}>
          <boxGeometry args={[item.w * 0.9, 0.34, item.d * 0.6]} />
          <meshStandardMaterial color={colors.get("var(--panel-3)")} transparent opacity={0.5} />
        </mesh>
      </mesh>
    );
  if (t === "pillar")
    return (
      <mesh position={[item.x, 1.4, item.z]} castShadow>
        <boxGeometry args={[0.45, 2.8, 0.45]} />
        <meshStandardMaterial color={colors.get("var(--idle)")} roughness={0.9} />
      </mesh>
    );
  if (t === "storage")
    return (
      <mesh position={[item.x, 0.75, item.z]} castShadow receiveShadow>
        <boxGeometry args={[item.w * 0.9, 1.5, item.d * 0.75]} />
        <meshStandardMaterial color={colors.get("color-mix(in srgb, var(--violet) 40%, var(--panel-3))")} roughness={0.7} />
        <Edges color={colors.get("var(--line-strong)")} />
      </mesh>
    );
  if (t === "wall")
    return (
      <mesh position={[item.x, 1.3, item.z]} castShadow>
        <boxGeometry args={[item.w, 2.6, Math.min(item.d, 0.15)]} />
        <meshStandardMaterial color={colors.get("var(--muted)")} />
      </mesh>
    );
  const floorColor = t === "unassigned" ? colors.get("color-mix(in srgb, var(--ink) 10%, var(--panel-2))") : t === "door" ? colors.get("var(--warn)") : colors.get("color-mix(in srgb, var(--ink) 5%, var(--panel))");
  return (
    <mesh position={[item.x, 0.006, item.z]} rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
      <planeGeometry args={[item.w * 0.98, item.d * 0.98]} />
      <meshStandardMaterial color={floorColor} transparent opacity={t === "door" ? 0.5 : 1} />
    </mesh>
  );
}

export function Floor3D({
  plan,
  mode,
  selected,
  onSelect,
  view,
  showLabels,
}: {
  plan: FloorPlan;
  mode: ColorMode;
  selected: string | null;
  onSelect: (cell: FloorCell, link?: RackLink) => void;
  view: "perspective" | "top";
  showLabels: boolean;
}) {
  const theme = useTheme();
  const { items, width, depth } = useMemo(() => layout(plan), [plan]);
  const mm = useMemo(() => maxMetric(plan.cells), [plan.cells]);
  const [hover, setHover] = useState<Placed | null>(null);
  const exprs = useMemo(() => {
    const set = new Set<string>([
      "var(--panel-3)", "var(--accent)", "var(--line-strong)", "var(--idle)", "var(--muted)", "var(--warn)", "var(--scene-floor)", "var(--scene-grid)", "var(--bg)",
      "color-mix(in srgb, var(--info) 45%, var(--panel-3))", "color-mix(in srgb, var(--violet) 40%, var(--panel-3))",
      "color-mix(in srgb, var(--ink) 10%, var(--panel-2))", "color-mix(in srgb, var(--ink) 5%, var(--panel))",
      ...Object.values(STATUS_COLOR),
    ]);
    items.forEach((i) => {
      if (i.cell.type === "rack" || i.cell.type === "empty_rack") set.add(rackColor(i.cell, i.link, mode, mm));
    });
    return Array.from(set);
  }, [items, mode, mm]);
  const colors = useCssColors(exprs, theme);
  const size = Math.max(width, depth);

  return (
    <div className="relative h-full w-full">
      <Canvas shadows dpr={[1, 2]} gl={{ preserveDrawingBuffer: true, antialias: true }} camera={{ position: [width / 2 + size * 0.35, size * 0.75, depth / 2 + size * 0.95], fov: 42, near: 0.1, far: 500 }} onPointerMissed={() => setHover(null)}>
        <color attach="background" args={[colors.get("var(--bg)") ?? "#08110f"]} />
        <ambientLight intensity={theme === "dark" ? 0.35 : 0.5} />
        <hemisphereLight args={["#ffffff", colors.get("var(--scene-floor)") ?? "#222222", theme === "dark" ? 1.0 : 1.0]} />
        <directionalLight position={[width * 0.3, size * 1.4, depth * 0.2]} intensity={theme === "dark" ? 1.5 : 1.5} castShadow shadow-mapSize={[2048, 2048]} shadow-camera-left={-size} shadow-camera-right={size} shadow-camera-top={size} shadow-camera-bottom={-size} />
        <OrbitControls makeDefault enableDamping dampingFactor={0.12} maxPolarAngle={Math.PI / 2.15} minDistance={3} maxDistance={size * 3} target={[width / 2, 0, depth / 2]} />
        <CameraRig view={view} width={width} depth={depth} />

        <mesh rotation={[-Math.PI / 2, 0, 0]} position={[width / 2, 0, depth / 2]} receiveShadow>
          <planeGeometry args={[width + 2, depth + 2]} />
          <meshStandardMaterial color={colors.get("var(--scene-floor)")} roughness={1} />
        </mesh>
        <gridHelper
          args={[Math.max(width, depth) + 2, Math.max(width, depth) + 2, colors.get("var(--scene-grid)"), colors.get("var(--scene-grid)")]}
          position={[width / 2, 0.002, depth / 2]}
        />

        {items.map((it) =>
          it.cell.type === "rack" || it.cell.type === "empty_rack" ? (
            <RackMesh
              key={cellKey(it.cell.row, it.cell.col)}
              item={it}
              color={colors.get(rackColor(it.cell, it.link, mode, mm)) ?? "#888"}
              colors={colors}
              selected={selected === cellKey(it.cell.row, it.cell.col)}
              hovered={hover === it}
              onHover={setHover}
              onSelect={(i) => onSelect(i.cell, i.link)}
              showLabel={showLabels}
            />
          ) : (
            <StaticMesh key={cellKey(it.cell.row, it.cell.col)} item={it} colors={colors} />
          ),
        )}

        {plan.rows.map((r, i) => (
          <Html key={`r${r}`} position={[-0.6, 0, i + 0.5]} center zIndexRange={[5, 0]} style={{ pointerEvents: "none" }}>
            <span className="font-mono text-[11px] font-semibold text-muted">{r}</span>
          </Html>
        ))}
        {plan.columns.map((c, i) => (
          <Html key={`c${c}`} position={[i + 0.5, 0, -0.6]} center zIndexRange={[5, 0]} style={{ pointerEvents: "none" }}>
            <span className="font-mono text-[10px] text-faint">{c}</span>
          </Html>
        ))}
        {hover && (
          <Html position={[hover.x, RACK_H + 0.6, hover.z]} center zIndexRange={[20, 10]} style={{ pointerEvents: "none" }}>
            <div className="w-max max-w-[220px] rounded-lg border border-line bg-panel px-2.5 py-1.5 text-[11.5px] shadow-2xl">
              <p className="font-mono font-semibold">{hover.link?.rack_name ?? `${hover.cell.row}${hover.cell.col}`}</p>
              <p className="text-muted">
                {hover.cell.type === "empty_rack" ? "Empty rack" : hover.link?.rack ? `${hover.link.devices.length} devices · ${hover.link.rack.used_units}/${hover.link.rack.height}U` : "Not set up yet — click to create"}
              </p>
              {hover.cell.metric != null && (
                <p className="text-faint">
                  {hover.cell.metric} {plan.metric_label ?? ""}
                </p>
              )}
              {hover.link?.devices.some((d) => d.status !== "online") && (
                <p className="text-bad">{hover.link.devices.filter((d) => d.status === "offline").length} offline · {hover.link.devices.filter((d) => d.status === "alerting").length} alerting</p>
              )}
            </div>
          </Html>
        )}
      </Canvas>
    </div>
  );
}
