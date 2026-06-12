import { createClient } from "https://esm.sh/@supabase/supabase-js@2.45.4";
import * as XLSX from "https://esm.sh/xlsx@0.18.5";

const SMN_XLSX_URL =
  "https://smn.conagua.gob.mx/tools/RESOURCES/Monitor%20de%20Sequia%20en%20Mexico/MunicipiosSequia.xlsx";

const UA = "Mozilla/5.0 (compatible; TequioCivicBot/1.0; +https://tequio.app)";

interface Resultado {
  ok: boolean;
  fecha_corte?: string;
  estados_subidos?: number;
  municipios_procesados?: number;
  error?: string;
  diagnostico?: Record<string, unknown>;
}

function excelSerialToISO(serial: number): string {
  const ms = serial * 86400000;
  const epoch = Date.UTC(1899, 11, 30);
  const date = new Date(epoch + ms);
  return date.toISOString().slice(0, 10);
}

async function scrape(): Promise<Resultado> {
  let buf: ArrayBuffer;
  try {
    const r = await fetch(SMN_XLSX_URL, {
      headers: {
        "User-Agent": UA,
        Accept: "application/octet-stream,*/*",
        "Accept-Language": "es-MX,es;q=0.9",
      },
    });
    if (!r.ok) {
      return { ok: false, error: `HTTP ${r.status} al descargar XLSX` };
    }
    buf = await r.arrayBuffer();
    if (buf.byteLength < 10000) {
      return {
        ok: false,
        error: `XLSX sospechosamente pequeño: ${buf.byteLength} bytes`,
      };
    }
  } catch (e) {
    return { ok: false, error: `Network: ${(e as Error).message}` };
  }

  let wb;
  try {
    wb = XLSX.read(new Uint8Array(buf), { type: "array", cellDates: false });
  } catch (e) {
    return { ok: false, error: `Parse XLSX: ${(e as Error).message}` };
  }

  const sheetName = wb.SheetNames.find((n) => n.toUpperCase().includes("MUNICIPIO")) ||
    wb.SheetNames[0];
  const ws = wb.Sheets[sheetName];
  const rows: unknown[][] = XLSX.utils.sheet_to_json(ws, {
    header: 1,
    defval: null,
    raw: true,
  });

  if (rows.length < 10) {
    return { ok: false, error: `XLSX casi vacío: ${rows.length} filas` };
  }

  const header = rows[0] as unknown[];
  const FIRST_DATE_COL = 9;

  let lastCol = FIRST_DATE_COL;
  for (let c = header.length - 1; c >= FIRST_DATE_COL; c--) {
    let nonEmpty = 0;
    for (let r = 1; r < rows.length; r++) {
      const row = rows[r];
      if (Array.isArray(row) && c < row.length && row[c]) nonEmpty++;
    }
    if (nonEmpty > 100) {
      lastCol = c;
      break;
    }
  }

  const serial = header[lastCol];
  let fechaCorte: string;
  if (typeof serial === "number") {
    fechaCorte = excelSerialToISO(serial);
  } else if (serial instanceof Date) {
    fechaCorte = serial.toISOString().slice(0, 10);
  } else if (typeof serial === "string" && /^\d{4}-\d{2}-\d{2}/.test(serial)) {
    fechaCorte = serial.slice(0, 10);
  } else {
    fechaCorte = new Date().toISOString().slice(0, 10);
  }

  const agg: Record<
    string,
    { total: number; D0: number; D1: number; D2: number; D3: number; D4: number }
  > = {};

  let municipiosProcesados = 0;
  for (let r = 1; r < rows.length; r++) {
    const row = rows[r];
    if (!Array.isArray(row) || row.length <= lastCol) continue;
    const estado = row[4];
    if (!estado || typeof estado !== "string") continue;
    const cat = String(row[lastCol] || "").trim().toUpperCase();
    if (!agg[estado]) {
      agg[estado] = { total: 0, D0: 0, D1: 0, D2: 0, D3: 0, D4: 0 };
    }
    agg[estado].total++;
    municipiosProcesados++;
    if (cat === "D0" || cat === "D1" || cat === "D2" || cat === "D3" || cat === "D4") {
      agg[estado][cat]++;
    }
  }

  const payload = Object.entries(agg).map(([estado, c]) => {
    const pct = (k: "D0" | "D1" | "D2" | "D3" | "D4") =>
      c.total === 0 ? 0 : Math.round((100 * c[k] / c.total) * 100) / 100;
    return {
      fecha_corte: fechaCorte,
      estado: estado.slice(0, 80),
      pct_anomalo_seco: pct("D0"),
      pct_sequia_moderada: pct("D1"),
      pct_sequia_severa: pct("D2"),
      pct_sequia_extrema: pct("D3"),
      pct_sequia_excepcional: pct("D4"),
      fuente: "CONAGUA SMN Monitor de Sequia",
    };
  });

  if (payload.length === 0) {
    return {
      ok: false,
      error: "No se agregaron filas (¿estructura del XLSX cambió?)",
      diagnostico: {
        rows_count: rows.length,
        header_len: header.length,
        last_col: lastCol,
      },
    };
  }

  const supabaseUrl = Deno.env.get("SUPABASE_URL")!;
  const serviceKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;
  const supa = createClient(supabaseUrl, serviceKey);

  const { error } = await supa
    .from("monitor_sequia")
    .upsert(payload, { onConflict: "estado,fecha_corte" });

  if (error) {
    return {
      ok: false,
      error: `Supabase upsert: ${error.message}`,
      diagnostico: { code: error.code, details: error.details },
    };
  }

  return {
    ok: true,
    fecha_corte: fechaCorte,
    estados_subidos: payload.length,
    municipios_procesados: municipiosProcesados,
  };
}

Deno.serve(async (_req) => {
  try {
    const result = await scrape();
    return new Response(JSON.stringify(result, null, 2), {
      status: result.ok ? 200 : 500,
      headers: { "Content-Type": "application/json" },
    });
  } catch (e) {
    return new Response(
      JSON.stringify({ ok: false, error: (e as Error).message }, null, 2),
      { status: 500, headers: { "Content-Type": "application/json" } },
    );
  }
});
