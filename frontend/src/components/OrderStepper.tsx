"use client";

import { orderProgress } from "@/lib/order-ui";

// Stepper progres pesanan: Dibayar → Diproses → Dikirim → Selesai.
export default function OrderStepper({ status }: { status: string }) {
  if (status === "batal") {
    return <div className="alert alert-error" style={{ marginBottom: 0 }}>Pesanan dibatalkan.</div>;
  }
  const steps = orderProgress(status);
  const activeIdx = steps.findIndex((s) => !s.done);
  return (
    <div className="flex items-center" style={{ gap: 0 }}>
      {steps.map((s, i) => {
        const active = i === activeIdx;
        const on = s.done;
        return (
          <div key={s.label} className="flex items-center" style={{ flex: i < steps.length - 1 ? 1 : "0 0 auto" }}>
            <div className="flex flex-col items-center" style={{ gap: 4 }}>
              <div
                className="grid place-items-center"
                style={{
                  width: 26, height: 26, borderRadius: 99, fontSize: 12, fontWeight: 700,
                  background: on ? "var(--brand-600)" : "var(--paper)",
                  color: on ? "#fff" : active ? "var(--brand-700)" : "var(--ink-400)",
                  border: "2px solid " + (on || active ? "var(--brand-600)" : "var(--ink-200)"),
                }}
              >
                {on ? "✓" : i + 1}
              </div>
              <span style={{ fontSize: 10.5, color: on || active ? "var(--ink-700)" : "var(--ink-400)", whiteSpace: "nowrap" }}>{s.label}</span>
            </div>
            {i < steps.length - 1 && (
              <div style={{ flex: 1, height: 2, margin: "0 6px", marginBottom: 16, background: steps[i + 1].done ? "var(--brand-600)" : "var(--ink-200)" }} />
            )}
          </div>
        );
      })}
    </div>
  );
}
