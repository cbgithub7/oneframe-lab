// @ts-check
// A reader of numpy's .npz: a zip of .npy files, each stored or deflated, as np.savez and
// np.savez_compressed write them (numpy writes every entry with zip64 fields). Deflated entries are
// inflated by the platform's DecompressionStream, so the page carries no inflater of its own.
//
// It reads what the engine's nodes write: little-endian numbers and bools, in C order. Anything
// else is refused by name, never read wrong.

/**
 * @typedef {Float32Array | Float64Array | Int8Array | Uint8Array | Int16Array | Uint16Array
 *   | Int32Array | Uint32Array | BigInt64Array | BigUint64Array} Data
 * @typedef {{ dtype: string, shape: number[], data: Data }} NpyArray an array: for a bool, `data`
 *   holds 0 or 1 a byte
 */

/** Each dtype read, as numpy spells it, and the typed array that holds it. */
const DTYPES = /** @type {Record<string, { bytes: number, make: (b: ArrayBuffer, o: number, n: number) => Data }>} */ ({
  "|b1": { bytes: 1, make: (b, o, n) => new Uint8Array(b, o, n) },
  "|u1": { bytes: 1, make: (b, o, n) => new Uint8Array(b, o, n) },
  "|i1": { bytes: 1, make: (b, o, n) => new Int8Array(b, o, n) },
  "<i2": { bytes: 2, make: (b, o, n) => new Int16Array(b, o, n) },
  "<u2": { bytes: 2, make: (b, o, n) => new Uint16Array(b, o, n) },
  "<i4": { bytes: 4, make: (b, o, n) => new Int32Array(b, o, n) },
  "<u4": { bytes: 4, make: (b, o, n) => new Uint32Array(b, o, n) },
  "<f4": { bytes: 4, make: (b, o, n) => new Float32Array(b, o, n) },
  "<f8": { bytes: 8, make: (b, o, n) => new Float64Array(b, o, n) },
  "<i8": { bytes: 8, make: (b, o, n) => new BigInt64Array(b, o, n) },
  "<u8": { bytes: 8, make: (b, o, n) => new BigUint64Array(b, o, n) },
});

const EOCD = 0x06054b50;
const ZIP64_LOCATOR = 0x07064b50;
const ZIP64_EOCD = 0x06064b50;
const CENTRAL = 0x02014b50;
const LOCAL = 0x04034b50;
const FULL16 = 0xffff;
const FULL32 = 0xffffffff;

/** @param {DataView} view @param {number} at */
const u64 = (view, at) => Number(view.getBigUint64(at, true));

/** @param {string} message */
const bad = (message) => new Error(`Not an npz numpy wrote: ${message}`);

/**
 * Where the central directory is, and how many entries it holds, from the end record (zip64's
 * when numpy wrote one).
 * @param {DataView} view
 */
function directory(view) {
  const last = Math.max(0, view.byteLength - 22 - FULL16);
  let end = -1;
  for (let at = view.byteLength - 22; at >= last; at -= 1) {
    if (view.getUint32(at, true) === EOCD) {
      end = at;
      break;
    }
  }
  if (end < 0) throw bad("no end of central directory");
  let count = view.getUint16(end + 10, true);
  let offset = view.getUint32(end + 16, true);
  if ((count === FULL16 || offset === FULL32) && end >= 20 && view.getUint32(end - 20, true) === ZIP64_LOCATOR) {
    const at = u64(view, end - 20 + 8);
    if (view.getUint32(at, true) !== ZIP64_EOCD) throw bad("a zip64 locator that points nowhere");
    count = u64(view, at + 32);
    offset = u64(view, at + 48);
  }
  return { count, offset };
}

/**
 * Each entry of the central directory: its name, method, compressed size and data's offset.
 * @param {DataView} view
 */
function* entries(view) {
  const { count, offset } = directory(view);
  const names = new TextDecoder();
  let at = offset;
  for (let i = 0; i < count; i += 1) {
    if (view.getUint32(at, true) !== CENTRAL) throw bad("a broken central directory");
    const method = view.getUint16(at + 10, true);
    let size = view.getUint32(at + 20, true);
    let plain = view.getUint32(at + 24, true);
    const nameLength = view.getUint16(at + 28, true);
    const extraLength = view.getUint16(at + 30, true);
    const commentLength = view.getUint16(at + 32, true);
    let local = view.getUint32(at + 42, true);
    const name = names.decode(new Uint8Array(view.buffer, view.byteOffset + at + 46, nameLength));
    // zip64's extra field holds, in this order, each size or offset the entry wrote as all ones.
    for (let x = at + 46 + nameLength; x < at + 46 + nameLength + extraLength;) {
      const id = view.getUint16(x, true);
      const length = view.getUint16(x + 2, true);
      if (id === 0x0001) {
        let field = x + 4;
        const next = () => {
          const value = u64(view, field);
          field += 8;
          return value;
        };
        if (plain === FULL32) plain = next();
        if (size === FULL32) size = next();
        if (local === FULL32) local = next();
      }
      x += 4 + length;
    }
    if (view.getUint32(local, true) !== LOCAL) throw bad(`${name} has no local header`);
    const start = local + 30 + view.getUint16(local + 26, true) + view.getUint16(local + 28, true);
    yield { name, method, size, plain, start };
    at += 46 + nameLength + extraLength + commentLength;
  }
}

/**
 * Inflate raw deflate with the platform's DecompressionStream.
 * @param {Uint8Array} bytes
 * @returns {Promise<ArrayBuffer>}
 */
async function inflate(bytes) {
  const stream = new Blob([/** @type {Uint8Array<ArrayBuffer>} */ (bytes)]).stream()
    .pipeThrough(new DecompressionStream("deflate-raw"));
  return new Response(stream).arrayBuffer();
}

/**
 * One .npy file: its header's dtype and shape, and its data as a typed array over the same bytes,
 * copied only when its offset is not aligned for its dtype (numpy aligns it).
 * @param {Uint8Array<ArrayBuffer>} bytes @param {string} name
 * @returns {NpyArray}
 */
export function readNpy(bytes, name = "array") {
  const magic = [0x93, 0x4e, 0x55, 0x4d, 0x50, 0x59]; // \x93NUMPY
  if (bytes.length < 10 || magic.some((b, i) => bytes[i] !== b)) throw bad(`${name} is not an .npy`);
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  const major = bytes[6];
  const headerLength = major === 1 ? view.getUint16(8, true) : view.getUint32(8, true);
  const headerStart = major === 1 ? 10 : 12;
  const header = new TextDecoder(major === 3 ? "utf-8" : "latin1")
    .decode(bytes.subarray(headerStart, headerStart + headerLength));
  const descr = /'descr':\s*'([^']+)'/.exec(header)?.[1];
  const fortran = /'fortran_order':\s*(True|False)/.exec(header)?.[1];
  const shapeText = /'shape':\s*\(([^)]*)\)/.exec(header)?.[1];
  if (descr === undefined || fortran === undefined || shapeText === undefined) throw bad(`${name}'s header`);
  if (fortran === "True") throw bad(`${name} is in Fortran order`);
  const dtype = DTYPES[descr];
  if (!dtype) throw bad(`${name} is of dtype ${descr}, which the page does not read`);
  const shape = shapeText.split(",").map((s) => s.trim()).filter(Boolean).map(Number);
  const count = shape.reduce((a, b) => a * b, 1);
  const start = headerStart + headerLength;
  const length = count * dtype.bytes;
  if (bytes.byteLength - start < length) throw bad(`${name} is shorter than its shape`);
  const offset = bytes.byteOffset + start;
  if (offset % dtype.bytes === 0) return { dtype: descr, shape, data: dtype.make(bytes.buffer, offset, count) };
  return { dtype: descr, shape, data: dtype.make(bytes.buffer.slice(offset, offset + length), 0, count) };
}

/**
 * Every array of an .npz, by its name (the entry's name without `.npy`). A stored entry's arrays
 * are views of `buffer`, never copies, so a large point map is held once.
 * @param {ArrayBuffer} buffer
 * @returns {Promise<Map<string, NpyArray>>}
 */
export async function readNpz(buffer) {
  const view = new DataView(buffer);
  /** @type {Map<string, NpyArray>} */
  const out = new Map();
  for (const entry of entries(view)) {
    const raw = new Uint8Array(buffer, entry.start, entry.size);
    let file;
    if (entry.method === 0) file = raw;
    else if (entry.method === 8) file = new Uint8Array(await inflate(raw));
    else throw bad(`${entry.name} is compressed by method ${entry.method}`);
    if (file.byteLength !== entry.plain) throw bad(`${entry.name} is not the size its entry says`);
    const name = entry.name.replace(/\.npy$/, "");
    out.set(name, readNpy(file, entry.name));
  }
  return out;
}

/**
 * A bool array's bytes, 1 where true; null for an array of another dtype, or none.
 * @param {NpyArray | undefined} array
 * @returns {Uint8Array | null}
 */
export function flags(array) {
  return array !== undefined && array.dtype === "|b1" ? /** @type {Uint8Array} */ (array.data) : null;
}
