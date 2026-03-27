/**
 * Minimal QR Code generator for OMADS LAN access modal.
 * Generates a QR code as an SVG string — no external dependencies.
 *
 * Based on the public-domain "QR Code Generator" algorithm (ISO 18004).
 * Supports alphanumeric + byte mode, error correction level M, versions 1-10.
 */

// prettier-ignore
const QR = (() => {
  // GF(256) log/exp tables for Reed-Solomon
  const EXP = new Uint8Array(256), LOG = new Uint8Array(256);
  for (let i = 0, v = 1; i < 255; i++) { EXP[i] = v; LOG[v] = i; v = (v << 1) ^ (v >= 128 ? 0x11d : 0); }
  EXP[255] = EXP[0];

  function rsMul(a, b) { return a && b ? EXP[(LOG[a] + LOG[b]) % 255] : 0; }

  function rsEncode(data, ecLen) {
    const gen = new Uint8Array(ecLen + 1);
    gen[0] = 1;
    for (let i = 0; i < ecLen; i++) {
      for (let j = i + 1; j >= 1; j--) gen[j] = gen[j] ^ rsMul(gen[j - 1], EXP[i]);
    }
    const out = new Uint8Array(ecLen);
    for (let i = 0; i < data.length; i++) {
      const fb = data[i] ^ out[0];
      for (let j = 0; j < ecLen - 1; j++) out[j] = out[j + 1] ^ rsMul(fb, gen[ecLen - j]);
      out[ecLen - 1] = rsMul(fb, gen[0]);
    }
    return out;
  }

  // Version capacities (byte mode, EC level M) and EC codewords per block
  const CAP = [0,16,28,44,64,86,108,124,154,182,216];
  const ECC = [0,10,16,26,18,24,28,26,30,22,28];
  const BLKS= [0, 1, 1, 1, 2, 2, 2, 2, 2, 2, 4];

  function pickVersion(len) {
    for (let v = 1; v <= 10; v++) if (len <= CAP[v]) return v;
    throw new Error("Data too long for QR (max ~216 bytes)");
  }

  function encode(text) {
    const bytes = new TextEncoder().encode(text);
    const ver = pickVersion(bytes.length);
    const size = ver * 4 + 17;
    const totalData = CAP[ver] + ECC[ver] * BLKS[ver];

    // Build data codewords (byte mode)
    const bits = [];
    const push = (val, len) => { for (let i = len - 1; i >= 0; i--) bits.push((val >> i) & 1); };
    push(0b0100, 4); // byte mode
    push(bytes.length, ver >= 10 ? 16 : 8); // char count
    for (const b of bytes) push(b, 8);
    push(0, Math.min(4, totalData * 8 - ECC[ver] * BLKS[ver] * 8 - bits.length)); // terminator
    while (bits.length % 8) bits.push(0);
    const dataCW = CAP[ver] + (totalData - CAP[ver] - ECC[ver] * BLKS[ver]);
    while (bits.length < dataCW * 8) {
      bits.push(...[1,1,1,0,1,1,0,0]); if (bits.length >= dataCW * 8) break;
      bits.push(...[0,0,0,1,0,0,0,1]);
    }
    bits.length = dataCW * 8;

    const codewords = new Uint8Array(dataCW);
    for (let i = 0; i < dataCW; i++) {
      let v = 0; for (let b = 0; b < 8; b++) v = (v << 1) | bits[i * 8 + b];
      codewords[i] = v;
    }

    // RS error correction
    const numBlocks = BLKS[ver];
    const ecPerBlock = ECC[ver];
    const dataPerBlock = Math.floor(dataCW / numBlocks);
    const allCW = [];
    let offset = 0;
    for (let b = 0; b < numBlocks; b++) {
      const blockData = codewords.slice(offset, offset + dataPerBlock);
      offset += dataPerBlock;
      const ec = rsEncode(blockData, ecPerBlock);
      allCW.push({ data: blockData, ec });
    }

    // Interleave
    const final = [];
    for (let i = 0; i < dataPerBlock; i++) for (const b of allCW) final.push(b.data[i]);
    for (let i = 0; i < ecPerBlock; i++) for (const b of allCW) final.push(b.ec[i]);

    // Place modules
    const grid = Array.from({ length: size }, () => new Int8Array(size)); // 0=white, 1=black, -1=unset
    const reserved = Array.from({ length: size }, () => new Uint8Array(size));

    function setMod(r, c, v) { grid[r][c] = v ? 1 : 0; reserved[r][c] = 1; }

    // Finder patterns
    function finder(r, c) {
      for (let dr = -1; dr <= 7; dr++) for (let dc = -1; dc <= 7; dc++) {
        const rr = r + dr, cc = c + dc;
        if (rr < 0 || rr >= size || cc < 0 || cc >= size) continue;
        const inOuter = dr >= 0 && dr <= 6 && dc >= 0 && dc <= 6;
        const inInner = dr >= 2 && dr <= 4 && dc >= 2 && dc <= 4;
        const onBorder = dr === 0 || dr === 6 || dc === 0 || dc === 6;
        setMod(rr, cc, inOuter && (onBorder || inInner));
      }
    }
    finder(0, 0); finder(0, size - 7); finder(size - 7, 0);

    // Timing patterns
    for (let i = 8; i < size - 8; i++) {
      setMod(6, i, i % 2 === 0);
      setMod(i, 6, i % 2 === 0);
    }

    // Dark module
    setMod(size - 8, 8, 1);

    // Reserve format info areas
    for (let i = 0; i < 8; i++) {
      if (!reserved[8][i]) { reserved[8][i] = 1; grid[8][i] = 0; }
      if (!reserved[8][size - 1 - i]) { reserved[8][size - 1 - i] = 1; grid[8][size - 1 - i] = 0; }
      if (!reserved[i][8]) { reserved[i][8] = 1; grid[i][8] = 0; }
      if (!reserved[size - 1 - i][8]) { reserved[size - 1 - i][8] = 1; grid[size - 1 - i][8] = 0; }
    }
    if (!reserved[8][8]) { reserved[8][8] = 1; grid[8][8] = 0; }

    // Place data bits
    const totalBits = final.length * 8;
    let bitIdx = 0;
    for (let right = size - 1; right >= 1; right -= 2) {
      if (right === 6) right = 5;
      for (let vert = 0; vert < size; vert++) {
        for (let j = 0; j < 2; j++) {
          const col = right - j;
          const upward = ((right + 1) / 2 | 0) % 2 === (size <= 25 ? 1 : 0);
          const row = upward ? size - 1 - vert : vert;
          if (reserved[row][col]) continue;
          grid[row][col] = bitIdx < totalBits ? (final[bitIdx >> 3] >> (7 - (bitIdx & 7))) & 1 : 0;
          bitIdx++;
        }
      }
    }

    // Apply mask 0 (checkerboard) and format info
    for (let r = 0; r < size; r++) for (let c = 0; c < size; c++) {
      if (!reserved[r][c] && (r + c) % 2 === 0) grid[r][c] ^= 1;
    }

    // Format info for mask 0, EC level M = 0b100000011001110  (pre-computed)
    const fmtBits = 0b100000011001110;
    const fmtPos = [
      [8,0],[8,1],[8,2],[8,3],[8,4],[8,5],[8,7],[8,8],
      [7,8],[5,8],[4,8],[3,8],[2,8],[1,8],[0,8]
    ];
    for (let i = 0; i < 15; i++) {
      const bit = (fmtBits >> i) & 1;
      const [r, c] = fmtPos[i]; grid[r][c] = bit;
    }
    // Second copy
    const fmtPos2 = [];
    for (let i = 0; i < 8; i++) fmtPos2.push([size - 1 - i, 8]);
    for (let i = 0; i < 7; i++) fmtPos2.push([8, size - 7 + i]);
    for (let i = 0; i < 15; i++) {
      const bit = (fmtBits >> i) & 1;
      const [r, c] = fmtPos2[i]; grid[r][c] = bit;
    }

    return { grid, size };
  }

  function toSVG(text, { scale = 4, margin = 2 } = {}) {
    const { grid, size } = encode(text);
    const full = size + margin * 2;
    let svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${full} ${full}" width="${full * scale}" height="${full * scale}">`;
    svg += `<rect width="${full}" height="${full}" fill="#fff"/>`;
    for (let r = 0; r < size; r++) for (let c = 0; c < size; c++) {
      if (grid[r][c]) svg += `<rect x="${c + margin}" y="${r + margin}" width="1" height="1" fill="#000"/>`;
    }
    svg += "</svg>";
    return svg;
  }

  return { toSVG };
})();

export default QR;
