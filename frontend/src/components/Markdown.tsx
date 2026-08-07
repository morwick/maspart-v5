"use client";

import { Fragment, type ReactNode } from "react";

import {
  MD_CARD_MIN_COLS,
  cardTitle,
  colClass,
  parseTableAt,
} from "./mdTable";

/**
 * Renderer Markdown ringan (tanpa dependency) untuk jawaban Asisten AI.
 * Mendukung: heading (#..######), tabel GFM, list (- / 1.), blockquote (>),
 * garis pemisah (---), serta inline **bold**, *italic*, `code`.
 *
 * TABEL memakai bahasa desain tabel aplikasi (`.tbl` di globals.css) + turunan
 * `.md-tbl` yang hanya menimpa TOKEN barisnya agar lebih rapat untuk bubble
 * chat. Parsing & keputusan per-kolom (rata kanan, font mono PN) ada di
 * `mdTable.ts` — file itu cermin `md_table.dart` di aplikasi Flutter.
 */

/* ── Inline: **bold**, *italic*, `code` ── */
function renderInline(text: string, keyBase: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  // Pecah berdasarkan token inline; tangani **, *, `.
  const re = /(\*\*[^*]+\*\*|`[^`]+`|\*[^*]+\*)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) nodes.push(text.slice(last, m.index));
    const tok = m[0];
    const key = `${keyBase}-${i++}`;
    if (tok.startsWith("**")) {
      nodes.push(<strong key={key}>{tok.slice(2, -2)}</strong>);
    } else if (tok.startsWith("`")) {
      nodes.push(
        <code
          key={key}
          style={{
            background: "var(--ink-100)",
            borderRadius: 4,
            padding: "1px 5px",
            fontSize: "0.88em",
            fontFamily: '"JetBrains Mono", ui-monospace, monospace',
          }}
        >
          {tok.slice(1, -1)}
        </code>,
      );
    } else {
      nodes.push(<em key={key}>{tok.slice(1, -1)}</em>);
    }
    last = m.index + tok.length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

export default function Markdown({ content }: { content: string }) {
  const lines = content.replace(/\r\n/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  let i = 0;
  let key = 0;

  const flushParas = (buf: string[]) => {
    if (!buf.length) return;
    blocks.push(
      <p key={`p-${key++}`} style={{ margin: "2px 0", lineHeight: 1.55 }}>
        {buf.map((ln, idx) => (
          <Fragment key={idx}>
            {idx > 0 && <br />}
            {renderInline(ln, `p${key}-${idx}`)}
          </Fragment>
        ))}
      </p>,
    );
    buf.length = 0;
  };

  let paras: string[] = [];

  while (i < lines.length) {
    const line = lines[i];
    const trimmed = line.trim();

    // Tabel GFM. Deteksi & keputusan per-kolom ada di mdTable.ts (dipakai juga
    // sbg spesifikasi port Flutter). Dicoba SEBELUM aturan '---' (hr): parser
    // menolak separator 1-sel, jadi '---' polos tetap jadi garis.
    const tabel = parseTableAt(lines, i);
    if (tabel) {
      flushParas(paras);
      const t = tabel.table;
      // >=4 kolom: di layar sempit tabel diganti kartu-per-baris (murni CSS →
      // markup server & klien identik, nol risiko hydration mismatch, dan draf
      // streaming tak perlu listener apa pun). Kartu hanya masuk DOM saat perlu.
      const wide = t.nCols >= MD_CARD_MIN_COLS;
      const judulCi = t.pnCol ?? 0;
      blocks.push(
        <div key={`t-${key++}`} className={`md-tbl-block${wide ? " is-wide" : ""}`}>
          <div className="surface md-tbl-wrap">
            <table className="tbl md-tbl">
              <thead>
                <tr>
                  {t.header.map((h, ci) => (
                    <th key={ci} scope="col" className={colClass(t, ci)}>
                      {renderInline(h, `th-${ci}`)}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {t.rows.map((r, ri) => (
                  <tr key={ri}>
                    {r.map((c, ci) => (
                      <td key={ci} className={colClass(t, ci)}>
                        {renderInline(c, `td-${ri}-${ci}`)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {wide && (
            <div className="md-cards">
              {t.rows.map((r, ri) => (
                <div className="md-card" key={ri}>
                  <div className={`md-card-title${t.pnCol !== null ? " pn" : ""}`}>
                    {renderInline(cardTitle(t, r, ri), `ct-${ri}`)}
                  </div>
                  {r.map((c, ci) => {
                    if (ci === judulCi || !c.trim()) return null;
                    const k = (t.header[ci] || "").trim() || `Kolom ${ci + 1}`;
                    return (
                      <div className="md-card-row" key={ci}>
                        <span className="md-card-k">{k}</span>
                        <span className={`md-card-v ${colClass(t, ci)}`}>
                          {renderInline(c, `cv-${ri}-${ci}`)}
                        </span>
                      </div>
                    );
                  })}
                </div>
              ))}
            </div>
          )}
        </div>,
      );
      i = tabel.next;
      continue;
    }

    // Heading
    const h = trimmed.match(/^(#{1,6})\s+(.*)$/);
    if (h) {
      flushParas(paras);
      const lvl = h[1].length;
      const size = [16, 15.5, 15, 14.5, 14, 13.5][lvl - 1];
      blocks.push(
        <div
          key={`h-${key++}`}
          style={{ fontWeight: 700, fontSize: size, margin: "10px 0 4px" }}
        >
          {renderInline(h[2], `h-${key}`)}
        </div>,
      );
      i++;
      continue;
    }

    // Garis pemisah
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(trimmed)) {
      flushParas(paras);
      blocks.push(
        <hr key={`hr-${key++}`} style={{ border: "none", borderTop: "1px solid var(--ink-150)", margin: "10px 0" }} />,
      );
      i++;
      continue;
    }

    // Blockquote
    if (trimmed.startsWith(">")) {
      flushParas(paras);
      const quote: string[] = [];
      while (i < lines.length && lines[i].trim().startsWith(">")) {
        quote.push(lines[i].trim().replace(/^>\s?/, ""));
        i++;
      }
      blocks.push(
        <blockquote
          key={`q-${key++}`}
          style={{
            // --brand-300 tak pernah didefinisikan → dulu jatuh ke fallback
            // hardcoded '#9ec' yang di luar palet & tak ikut mode gelap.
            borderLeft: "3px solid var(--brand-500)",
            background: "var(--ink-50)",
            padding: "6px 12px",
            margin: "8px 0",
            borderRadius: 6,
            color: "var(--ink-700)",
          }}
        >
          {quote.map((q, qi) => (
            <div key={qi}>{renderInline(q, `q-${qi}`)}</div>
          ))}
        </blockquote>,
      );
      continue;
    }

    // List (- / * / 1.)
    if (/^([-*]|\d+\.)\s+/.test(trimmed)) {
      flushParas(paras);
      const ordered = /^\d+\.\s+/.test(trimmed);
      const items: string[] = [];
      while (i < lines.length && /^([-*]|\d+\.)\s+/.test(lines[i].trim())) {
        items.push(lines[i].trim().replace(/^([-*]|\d+\.)\s+/, ""));
        i++;
      }
      const ListTag = ordered ? "ol" : "ul";
      blocks.push(
        <ListTag key={`l-${key++}`} style={{ margin: "4px 0", paddingLeft: 22, lineHeight: 1.5 }}>
          {items.map((it, ii) => (
            <li key={ii} style={{ margin: "2px 0" }}>
              {renderInline(it, `li-${ii}`)}
            </li>
          ))}
        </ListTag>,
      );
      continue;
    }

    // Baris kosong → akhiri paragraf
    if (trimmed === "") {
      flushParas(paras);
      i++;
      continue;
    }

    // Teks biasa → kumpulkan sebagai paragraf
    paras.push(trimmed);
    i++;
  }
  flushParas(paras);

  return <div style={{ fontSize: 14 }}>{blocks}</div>;
}
