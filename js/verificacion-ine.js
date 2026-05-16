/* Tequio Verificación INE — OCR 100% local
 * La foto NUNCA sale del dispositivo. Solo se genera un hash SHA256 anónimo.
 */
(function () {
  'use strict';

  const STORAGE_KEY = 'tequio_ine_v1';
  const TESSERACT_CDN = 'https://cdn.jsdelivr.net/npm/tesseract.js@5/dist/tesseract.min.js';

  // Cargar Tesseract.js lazy
  let _tesseractLoaded = null;
  function cargarTesseract() {
    if (_tesseractLoaded) return _tesseractLoaded;
    _tesseractLoaded = new Promise((resolve, reject) => {
      if (window.Tesseract) return resolve(window.Tesseract);
      const s = document.createElement('script');
      s.src = TESSERACT_CDN;
      s.onload = () => resolve(window.Tesseract);
      s.onerror = () => reject(new Error('Tesseract.js no se pudo cargar'));
      document.head.appendChild(s);
    });
    return _tesseractLoaded;
  }

  async function sha256(s) {
    const buf = new TextEncoder().encode(s);
    const hash = await crypto.subtle.digest('SHA-256', buf);
    return Array.from(new Uint8Array(hash)).map(b => b.toString(16).padStart(2, '0')).join('');
  }

  // Pre-procesa imagen: aumentar contraste, escala de grises
  async function preprocesar(fileOrDataUrl) {
    return new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => {
        const canvas = document.createElement('canvas');
        // Limit max dimension to 1600px (Tesseract is slow on huge images)
        const MAX = 1600;
        const scale = Math.min(1, MAX / Math.max(img.width, img.height));
        canvas.width = Math.round(img.width * scale);
        canvas.height = Math.round(img.height * scale);
        const ctx = canvas.getContext('2d');
        ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
        // Aumentar contraste
        const imgData = ctx.getImageData(0, 0, canvas.width, canvas.height);
        const d = imgData.data;
        for (let i = 0; i < d.length; i += 4) {
          const gray = (d[i] * 0.299 + d[i+1] * 0.587 + d[i+2] * 0.114);
          const v = gray > 128 ? 255 : gray < 80 ? 0 : gray;
          d[i] = d[i+1] = d[i+2] = v;
        }
        ctx.putImageData(imgData, 0, 0);
        resolve(canvas.toDataURL('image/png'));
      };
      img.onerror = () => reject(new Error('No se pudo cargar la imagen'));
      if (typeof fileOrDataUrl === 'string') {
        img.src = fileOrDataUrl;
      } else {
        const r = new FileReader();
        r.onload = (e) => { img.src = e.target.result; };
        r.onerror = () => reject(new Error('No se pudo leer el archivo'));
        r.readAsDataURL(fileOrDataUrl);
      }
    });
  }

  // Extraer campos del texto OCR crudo
  function parsearINE(textoOcr) {
    const limpio = textoOcr.toUpperCase().replace(/\s+/g, ' ');

    // Clave de Elector: 6 letras + 8 dígitos + H/M + 3 dígitos = 18 chars
    const claveMatch = limpio.match(/[A-Z]{6}\d{8}[HM]\d{3}/);
    const claveElector = claveMatch ? claveMatch[0] : null;

    // CIC: típicamente etiquetado "CIC" o "IDMEX" + 10 dígitos
    let cic = null;
    const cicLabel = limpio.match(/(?:CIC|IDMEX)[^\d]*(\d{10})/);
    if (cicLabel) cic = cicLabel[1];

    // OCR de la MRZ inferior: 13 dígitos consecutivos
    const ocrMatch = limpio.match(/\b\d{13}\b/);
    const ocr = ocrMatch ? ocrMatch[0] : null;

    return { clave_elector: claveElector, cic, ocr };
  }

  // API principal
  async function verificar(file, onProgress) {
    onProgress = onProgress || (() => {});
    onProgress({ etapa: 'preparando', pct: 0 });

    // 1. Preprocesar
    const dataUrl = await preprocesar(file);
    onProgress({ etapa: 'cargando_motor_ocr', pct: 15 });

    // 2. Cargar Tesseract
    const Tesseract = await cargarTesseract();
    onProgress({ etapa: 'reconociendo', pct: 30 });

    // 3. OCR con worker
    const worker = await Tesseract.createWorker('spa', 1, {
      logger: m => {
        if (m.status === 'recognizing text') {
          onProgress({ etapa: 'reconociendo', pct: 30 + Math.round(m.progress * 60) });
        }
      }
    });

    const result = await worker.recognize(dataUrl);
    await worker.terminate();
    onProgress({ etapa: 'analizando', pct: 90 });

    // 4. Parsear
    const campos = parsearINE(result.data.text);

    if (!campos.clave_elector && !campos.cic) {
      onProgress({ etapa: 'fallo', pct: 100 });
      return { ok: false, motivo: 'no_se_detecto_clave', texto_raw: result.data.text.slice(0, 200) };
    }

    // 5. Generar hash canónico
    // Prefer clave_elector, fallback a cic+ocr
    const canonico = campos.clave_elector || ((campos.cic || '') + (campos.ocr || ''));
    if (!canonico || canonico.length < 10) {
      return { ok: false, motivo: 'datos_insuficientes' };
    }

    const ine_hash = await sha256('tequio-ine-v1:' + canonico);
    const preview = canonico.slice(0, 4) + '••••' + canonico.slice(-2);

    // 6. Guardar local
    const record = { ine_hash, preview, verificado_at: new Date().toISOString() };
    try { localStorage.setItem(STORAGE_KEY, JSON.stringify(record)); } catch (_) {}

    onProgress({ etapa: 'ok', pct: 100 });
    return { ok: true, ...record };
  }

  function getCached() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      return raw ? JSON.parse(raw) : null;
    } catch (_) { return null; }
  }

  function clearCached() {
    try { localStorage.removeItem(STORAGE_KEY); } catch (_) {}
  }

  // Exponer en AntiBot.INE si existe, si no en window.AntiBotINE
  const target = (window.AntiBot = window.AntiBot || {});
  target.INE = { verificar, getCached, clearCached };
})();
