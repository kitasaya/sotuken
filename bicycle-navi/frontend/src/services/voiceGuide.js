/**
 * 音声案内サービス（Web Speech API ラッパー）
 * フェーズ5-2: 音声案内の実装
 */

// GraphHopper instruction sign → 音声案内テキスト
const SIGN_TO_TEXT = {
  "-3": "鋭く左折してください",
  "-2": "左折してください",
  "-1": "やや左に進んでください",
  0: "直進してください",
  1: "やや右に進んでください",
  2: "右折してください",
  3: "鋭く右折してください",
  4: "目的地に到着です",
  5: "Uターンしてください",
  6: "ロータリーに進んでください",
  "-6": "ロータリーに進んでください",
};

/**
 * 距離を音声向けテキストに変換
 */
const distToText = (meters) => {
  if (meters == null || meters <= 0) return "";
  if (meters >= 1000) return `${(meters / 1000).toFixed(1)}キロメートル先、`;
  return `${Math.round(meters)}メートル先、`;
};

/**
 * instruction から音声案内テキストを生成
 * @param {object} instruction - GraphHopper instruction
 * @param {boolean} isTwoStep - 二段階右折が必要か
 * @returns {string}
 */
export const buildAnnouncementText = (instruction, isTwoStep = false) => {
  if (!instruction) return "";
  const sign = instruction.sign ?? 0;
  const dist = instruction.distance;
  const street = instruction.street_name || "";

  // 二段階右折
  // 設計判断ポイント2: 暫定文言。「左端に寄り直進→右折」の手順を案内。
  if (isTwoStep) {
    return (
      `${distToText(dist)}二段階右折です。` +
      "一度左端に寄り、そのまま交差点を直進してから右折してください"
    );
  }

  let text = distToText(dist);
  text += SIGN_TO_TEXT[String(sign)] || "直進してください";
  if (street && sign !== 4) text += `。${street}方向です`;
  return text;
};

/**
 * 事前通知テキストを生成（交差点手前での予告案内）
 * フェーズ5-3のGPS統合で呼び出し予定。
 * @param {number} distanceRemaining - 次の交差点までの残り距離(m)
 * @param {object} nextInstruction - 次の instruction
 * @param {boolean} isTwoStep
 * @returns {string|null} 案内が必要な場合はテキスト、不要なら null
 */
export const buildApproachText = (
  distanceRemaining,
  nextInstruction,
  isTwoStep = false
) => {
  if (!nextInstruction || distanceRemaining == null) return null;
  const sign = nextInstruction.sign ?? 0;

  // 目的地到着・直進は事前通知不要
  if (sign === 0 || sign === 4) return null;

  const dirText =
    SIGN_TO_TEXT[String(sign)]?.replace("してください", "です") || "曲がります";

  if (distanceRemaining <= 30) {
    return isTwoStep ? "まもなく二段階右折です" : `まもなく${dirText}`;
  }
  if (distanceRemaining <= 100) {
    const dist = Math.round(distanceRemaining);
    return isTwoStep
      ? `${dist}メートル先、二段階右折です`
      : `${dist}メートル先、${dirText}`;
  }
  return null;
};

/**
 * リルート理由の音声テキストを生成
 * @param {Array} violations
 * @returns {string|null}
 */
export const buildRerouteText = (violations) => {
  if (!violations?.length) return null;
  const types = new Set(violations.map((v) => v.rule));
  const reasons = [];
  if (types.has("oneway")) reasons.push("一方通行");
  if (types.has("two_step_turn")) reasons.push("二段階右折が必要な交差点");
  if (reasons.length === 0) return null;
  return `法規に合わせてルートを変更しました。${reasons.join("・")}のため迂回します`;
};

/**
 * VoiceGuide クラス（シングルトン）
 * Web Speech API の SpeechSynthesis をラップする。
 */
class VoiceGuide {
  constructor() {
    this._enabled = true;
    this._supported =
      typeof window !== "undefined" && "speechSynthesis" in window;
  }

  get isSupported() {
    return this._supported;
  }

  get enabled() {
    return this._enabled;
  }

  setEnabled(val) {
    this._enabled = Boolean(val);
    if (!val) this.cancel();
  }

  /**
   * テキストを読み上げる。既存の発話は中断してから開始する。
   * @param {string} text
   */
  speak(text) {
    if (!this._enabled || !this._supported || !text) return;
    window.speechSynthesis.cancel();
    const utt = new SpeechSynthesisUtterance(text);
    utt.lang = "ja-JP";
    utt.rate = 1.0;
    utt.pitch = 1.0;
    utt.volume = 1.0;
    window.speechSynthesis.speak(utt);
  }

  cancel() {
    if (this._supported) window.speechSynthesis.cancel();
  }
}

// モジュール全体で共有するシングルトンインスタンス
const voiceGuide = new VoiceGuide();
export default voiceGuide;
