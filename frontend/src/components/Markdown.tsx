"use client";

import { Fragment, type ReactNode } from "react";

/**
 * Renderer Markdown ringan (tanpa dependency) untuk jawaban Asisten AI.
 * Mendukung: heading (#..######), tabel GFM, list (- / 1.), blockquote (>),
 * garis pemisah (---), serta inline **bold**, *italic*, `code`.
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

function splitRow(line: string): string[] {
  let s = line.trim();
  if (s.startsWith("|")) s = s.slice(1);
  if (s.endsWith("|")) s = s.slice(0, -1);
  return s.split("|").map((c) => c.trim());
}

const isTableSep = (line: string) => /^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$/.test(line) && line.includes("-");

// Sel tabel yang berupa Part Number/kode (huruf besar+angka, boleh . / + -) →
// ditampilkan dengan font mono agar mudah dibaca & disalin persis.
const isPnCell = (s: string) =>
  /^[A-Z0-9][A-Z0-9./+\-]{4,}$/.test(s.replace(/\*\*/g, "").trim());

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

    // Tabel GFM: baris '|' diikuti baris separator '---'
    if (trimmed.startsWith("|") && i + 1 < lines.length && isTableSep(lines[i + 1])) {
      flushParas(paras);
      const header = splitRow(line);
      i += 2; // lewati header + separator
      const rows: string[][] = [];
      while (i < lines.length && lines[i].trim().startsWith("|")) {
        rows.push(splitRow(lines[i]));
        i++;
      }
      blocks.push(
        <div key={`t-${key++}`} style={{ overflowX: "auto", margin: "8px 0" }}>
          <table style={{ borderCollapse: "collapse", width: "100%", fontSize: 12.5 }}>
            <thead>
              <tr>
                {header.map((h, hi) => (
                  <th
                    key={hi}
                    style={{
                      textAlign: "left",
                      padding: "6px 10px",
                      background: "var(--ink-100)",
                      borderBottom: "2px solid var(--ink-200)",
                      whiteSpace: "nowrap",
                      fontWeight: 650,
                    }}
                  >
                    {renderInline(h, `th-${hi}`)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, ri) => (
                <tr key={ri} style={{ background: ri % 2 ? "var(--ink-50)" : "transparent" }}>
                  {header.map((_, ci) => {
                    const cell = r[ci] ?? "";
                    const pn = isPnCell(cell);
                    return (
                      <td
                        key={ci}
                        style={{
                          padding: "6px 10px",
                          borderBottom: "1px solid var(--ink-150)",
                          verticalAlign: "top",
                          ...(pn
                            ? {
                                fontFamily: '"JetBrains Mono", ui-monospace, monospace',
                                fontSize: 12,
                                whiteSpace: "nowrap",
                                color: "var(--ink-900)",
                              }
                            : null),
                        }}
                      >
                        {renderInline(cell, `td-${ri}-${ci}`)}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>,
      );
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
            borderLeft: "3px solid var(--brand-300, #9ec)",
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
