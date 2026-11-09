// @ts-check
// An Image: the picture itself, read through the value protocol.

export const TYPE = "Image";

/** @param {import("../viewer.js").Show} show @returns {Promise<import("../viewer.js").Shown>} */
export async function show({ host, value, url }) {
  const img = document.createElement("img");
  img.className = "picture";
  img.alt = String(value.meta.source ?? "The picture this output holds");
  img.src = url;
  await img.decode();
  host.replaceChildren(img);
  return { summary: `${img.naturalWidth} x ${img.naturalHeight} px` };
}
