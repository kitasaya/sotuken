import { useCallback, useEffect, useRef, useState } from "react";

const SHEET_MAX_VH = 0.9;

const SNAP_RATIOS = {
  peek: 0.18,
  half: 0.5,
  full: 0.9,
};

const SNAP_ORDER = ["peek", "half", "full"];

const TAP_THRESHOLD_PX = 5;

const clamp = (v, min, max) => Math.max(min, Math.min(max, v));

export default function BottomSheet({ snap = "half", onSnapChange, children }) {
  const [winH, setWinH] = useState(() =>
    typeof window !== "undefined" ? window.innerHeight : 800
  );
  const handleRef = useRef(null);
  const dragStateRef = useRef(null);
  const [dragTranslate, setDragTranslate] = useState(null);

  useEffect(() => {
    const onResize = () => setWinH(window.innerHeight);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  const snapTranslate = useCallback(
    (s) => (SHEET_MAX_VH - SNAP_RATIOS[s]) * winH,
    [winH]
  );

  const currentTranslate = dragTranslate ?? snapTranslate(snap);
  // ドラッグ中（dragTranslate が数値）はアニメーション抑止、確定（null）後は補間
  const dragging = dragTranslate != null;

  const handlePointerDown = useCallback(
    (e) => {
      if (e.button != null && e.button !== 0 && e.pointerType === "mouse") return;
      const handle = handleRef.current;
      if (!handle) return;
      handle.setPointerCapture(e.pointerId);
      dragStateRef.current = {
        pointerId: e.pointerId,
        startY: e.clientY,
        startTranslate: snapTranslate(snap),
        moved: false,
      };
      setDragTranslate(snapTranslate(snap));
    },
    [snap, snapTranslate]
  );

  const handlePointerMove = useCallback(
    (e) => {
      const drag = dragStateRef.current;
      if (!drag || drag.pointerId !== e.pointerId) return;
      const delta = e.clientY - drag.startY;
      if (Math.abs(delta) > TAP_THRESHOLD_PX) drag.moved = true;
      const minTranslate = snapTranslate("full");
      const maxTranslate = snapTranslate("peek");
      const next = clamp(drag.startTranslate + delta, minTranslate, maxTranslate);
      setDragTranslate(next);
    },
    [snapTranslate]
  );

  const finishDrag = useCallback(
    (e) => {
      const drag = dragStateRef.current;
      if (!drag || drag.pointerId !== e.pointerId) return;
      try {
        handleRef.current?.releasePointerCapture(e.pointerId);
      } catch {
        // pointer already released
      }
      const finalTranslate = dragTranslate ?? snapTranslate(snap);
      const wasTap = !drag.moved;
      dragStateRef.current = null;

      if (wasTap) {
        const idx = SNAP_ORDER.indexOf(snap);
        const nextSnap = SNAP_ORDER[(idx + 1) % SNAP_ORDER.length];
        onSnapChange?.(nextSnap);
        setDragTranslate(null);
        return;
      }

      let bestSnap = snap;
      let bestDist = Infinity;
      for (const s of SNAP_ORDER) {
        const d = Math.abs(snapTranslate(s) - finalTranslate);
        if (d < bestDist) {
          bestDist = d;
          bestSnap = s;
        }
      }
      onSnapChange?.(bestSnap);
      setDragTranslate(null);
    },
    [dragTranslate, onSnapChange, snap, snapTranslate]
  );

  return (
    <div
      className="bottom-sheet"
      style={{
        height: `${SHEET_MAX_VH * 100}vh`,
        transform: `translateY(${currentTranslate}px)`,
        transition: dragging
          ? "none"
          : "transform 0.28s cubic-bezier(0.32, 0.72, 0, 1)",
      }}
      aria-hidden={false}
    >
      <div
        ref={handleRef}
        className="sheet-handle-area"
        onPointerDown={handlePointerDown}
        onPointerMove={handlePointerMove}
        onPointerUp={finishDrag}
        onPointerCancel={finishDrag}
        role="button"
        tabIndex={0}
        aria-label="ボトムシートを開閉"
      >
        <div className="sheet-handle" />
      </div>
      <div className="sheet-body">{children}</div>
    </div>
  );
}
