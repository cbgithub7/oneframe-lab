// @ts-check
// The page's one false-colour map, fixed and perceptual: a polynomial fit of viridis (dark violet
// to yellow), which stays readable in grey and to most colour-blind eyes. The numbers a colour
// stands for are always written beside it, so nothing is said by colour alone.

const C = [
  [0.2777273272234177, 0.005407344544966578, 0.3340998053353061],
  [0.1050930431085774, 1.404613529898575, 1.384590162594685],
  [-0.3308618287255563, 0.214847559468213, 0.09509516302823659],
  [-4.634230498983486, -5.799100973351585, -19.33244095627987],
  [6.228269936347081, 14.17993336680509, 56.69055260068105],
  [4.776384997670288, -13.74514537774601, -65.35303263337234],
  [-5.435455855934631, 4.645852612178535, 26.3124352495832],
];

/** 256 colours, RGB bytes, from t = 0 to t = 1. */
export const RAMP = (() => {
  const out = new Uint8Array(256 * 3);
  for (let i = 0; i < 256; i += 1) {
    const t = i / 255;
    for (let c = 0; c < 3; c += 1) {
      let v = C[6][c];
      for (let k = 5; k >= 0; k -= 1) v = C[k][c] + t * v;
      out[i * 3 + c] = Math.round(Math.min(1, Math.max(0, v)) * 255);
    }
  }
  return out;
})();

/**
 * The colour index of a value within [lo, hi], near ones brightest.
 * @param {number} value @param {number} lo @param {number} hi
 */
export function rampIndex(value, lo, hi) {
  const t = hi > lo ? (value - lo) / (hi - lo) : 0;
  return Math.round((1 - Math.min(1, Math.max(0, t))) * 255);
}

/**
 * The range of the finite, positive values where `valid` is not 0, at a stride; [0, 0] when none.
 * The 2nd and 98th percentiles of a sample, so a few stray pixels do not wash out the rest.
 * @param {ArrayLike<number>} values @param {ArrayLike<number> | null} valid @param {number} [every]
 * @returns {[number, number]}
 */
export function robustRange(values, valid, every = 1) {
  const sample = [];
  const step = Math.max(every, Math.ceil(values.length / 200_000));
  for (let i = 0; i < values.length; i += step) {
    const v = values[i];
    if ((valid === null || valid[i]) && Number.isFinite(v) && v > 0) sample.push(v);
  }
  if (sample.length === 0) return [0, 0];
  sample.sort((a, b) => a - b);
  return [sample[Math.floor(sample.length * 0.02)], sample[Math.min(sample.length - 1, Math.floor(sample.length * 0.98))]];
}
