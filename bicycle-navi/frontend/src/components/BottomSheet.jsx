import { useCallback, useEffect, useRef, useState } from "react";

const SHEET_MAX_VH = 0.9;

const SNAP_RATIOS = {
  peek: 0.18,
  half: 0.5,
  // full 時も画面上部に top-bar / セーフエリア分の隙間を残す（下の MIN_TOP_GAP_PX と併用）
  full: 0.78,
};

const SNAP_ORDER = ["peek", "half", "full"];

const TAP_THRESHOLD_PX = 5;
// full 時にシート上端（ハンドル）が top-bar の裏に隠れて操作不能にならないための最小余白
const MIN_TOP_GAP_PX = 72;

const clamp = (v, min, max) => Math.max(min, Math.min(max, v));

const isInteractiveTarget = (target) =>
  !!target?.closest?.(
    'input, textarea, select, button, a, [role="button"], [contenteditable="true"]'
  );

export default function BottomSheet({ snap = "half", onSnapChange, children }) {
  const [winH, setWinH] = useState(() =>
    typeof window !== "undefined" ? window.innerHeight : 800
  );
  const handleRef = useRef(null);
  const bodyRef = useRef(null);
  const dragStateRef = useRef(null);
  const [dragTranslate, setDragTranslate] = useState(null);

  useEffect(() => {
    const onResize = () => setWinH(window.innerHeight);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  const snapTranslate = useCallback(
    (s) => Math.max(MIN_TOP_GAP_PX, (SHEET_MAX_VH - SNAP_RATIOS[s]) * winH),
    [winH]
  );

  const currentTranslate = dragTranslate ?? snapTranslate(snap);
  // ドラッグ中（dragTranslate が数値）はアニメーション抑止、確定（null）後は補間
  const dragging = dragTranslate != null;

  const startDrag = useCallback(
    (e, el) => {
      if (e.button != null && e.button !== 0 && e.pointerType === "mouse") return;
      try {
        el.setPointerCapture(e.pointerId);
      } catch {
        // pointer capture 非対応環境
      }
      // sheet-body はデフォルトで縦スクロール可能（touch-action: auto）なため、
      // ドラッグ開始が確定した瞬間に touch-action を止めてブラウザ標準のスクロールと競合しないようにする。
      // React の再レンダーを待たずに同期的に設定する必要がある（次の touchmove より前に反映させるため）。
      el.style.touchAction = "none";
      dragStateRef.current = {
        pointerId: e.pointerId,
        sourceEl: el,
        startY: e.clientY,
        startTranslate: snapTranslate(snap),
        moved: false,
      };
      setDragTranslate(snapTranslate(snap));
    },
    [snap, snapTranslate]
  );

  const handlePointerDown = useCallback(
    (e) => {
      if (!handleRef.current) return;
      startDrag(e, handleRef.current);
    },
    [startDrag]
  );

  // 検索モードの本文余白（入力欄やボタン以外の部分）からもシートをドラッグできるようにする。
  // すでに一番上までスクロールされている場合のみドラッグ開始とし、リストのスクロール操作と衝突しないようにする。
  const handleBodyPointerDown = useCallback(
    (e) => {
      const body = bodyRef.current;
      if (!body) return;
      if (isInteractiveTarget(e.target)) return;
      if (body.scrollTop > 0) return;
      startDrag(e, body);
    },
    [startDrag]
  );

  useEffect(() => {
    const handleMove = (e) => {
      const drag = dragStateRef.current;
      if (!drag || drag.pointerId !== e.pointerId) return;
      const delta = e.clientY - drag.startY;
      if (Math.abs(delta) > TAP_THRESHOLD_PX) drag.moved = true;
      const minTranslate = snapTranslate("full");
      const maxTranslate = snapTranslate("peek");
      const next = clamp(drag.startTranslate + delta, minTranslate, maxTranslate);
      setDragTranslate(next);
    };

    const handleUp = (e) => {
      const drag = dragStateRef.current;
      if (!drag || drag.pointerId !== e.pointerId) return;
      try {
        drag.sourceEl?.releasePointerCapture(e.pointerId);
      } catch {
        // pointer already released
      }
      if (drag.sourceEl) drag.sourceEl.style.touchAction = "";
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
    };

    window.addEventListener("pointermove", handleMove);
    window.addEventListener("pointerup", handleUp);
    window.addEventListener("pointercancel", handleUp);
    return () => {
      window.removeEventListener("pointermove", handleMove);
      window.removeEventListener("pointerup", handleUp);
      window.removeEventListener("pointercancel", handleUp);
    };
  }, [dragTranslate, onSnapChange, snap, snapTranslate]);

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
        role="button"
        tabIndex={0}
        aria-label="ボトムシートを開閉"
      >
        <div className="sheet-handle" />
      </div>
      <div ref={bodyRef} className="sheet-body" onPointerDown={handleBodyPointerDown}>
        {children}
      </div>
    </div>
  );
}
