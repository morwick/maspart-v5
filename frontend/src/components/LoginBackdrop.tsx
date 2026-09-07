"use client";

/**
 * Latar panel merek halaman /login: medan baut, mur & ring yang hanyut pelan.
 *
 * Kenapa prosedural (bukan file model): aset 3D EPC yang kita punya berformat
 * `.pvz` PTC Creo View — geometrinya `.ol`, format tertutup yang tak bisa dibaca
 * three.js (itu sebabnya /part memakai engine ThingView WASM, bukan three).
 * Semua bentuk di sini dibangun dari kode: nol file model, nol permintaan
 * jaringan, dan latar ini tak bisa gagal-muat.
 *
 * three.js di-`import()` DI DALAM effect supaya masuk chunk terpisah — halaman
 * kerja staf gudang (cari part, stok, asisten) tak ikut menanggung ~150 KB itu.
 *
 * Pagar yang wajib ada (staf sering membuka dari HP kelas menengah di gudang):
 *   • `prefers-reduced-motion` → satu bingkai statis, rAF berhenti TOTAL;
 *   • tab tak aktif → loop berhenti, bukan sekadar tak terlihat;
 *   • devicePixelRatio dibatasi (di HP dpr 3 berarti 9× piksel);
 *   • WebGL gagal/ditolak → latar CSS `.login-fallback` yang tetap terlihat.
 */

import { useEffect, useRef } from "react";

// Batas medan dalam satuan dunia; kamera di z=17 dengan fov 38°.
const BOUND = { x: 15, y: 10, zNear: 4, zFar: -20 };

export default function LoginBackdrop() {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    const host = canvas?.parentElement;
    if (!canvas || !host) return;

    let dead = false;
    let teardown = () => {};

    (async () => {
      const THREE = await import("three");
      if (dead) return;

      let renderer: import("three").WebGLRenderer;
      try {
        renderer = new THREE.WebGLRenderer({
          canvas,
          antialias: true,
          alpha: false,
          powerPreference: "high-performance",
        });
      } catch {
        return; // latar CSS di bawah kanvas tetap tampil
      }
      if (!renderer.getContext()) return;

      renderer.toneMapping = THREE.ACESFilmicToneMapping;
      renderer.toneMappingExposure = 1.06;

      const BG = 0x0f1411; // --ink-900, sama dengan sidebar Command Center
      const scene = new THREE.Scene();
      scene.background = new THREE.Color(BG);
      scene.fog = new THREE.Fog(BG, 16, 38);

      const camera = new THREE.PerspectiveCamera(38, 1, 0.1, 100);
      camera.position.set(0, 0, 17);

      // Lingkungan reflektif digambar dari kanvas 2D (ekuirektangular 128×64):
      // logam tanpa envMap tampil hitam mati, dan memuat HDRI berarti satu
      // permintaan jaringan lagi di halaman yang harus cepat.
      const envSource = () => {
        const c = document.createElement("canvas");
        c.width = 128;
        c.height = 64;
        const g = c.getContext("2d")!;
        const sky = g.createLinearGradient(0, 0, 0, 64);
        sky.addColorStop(0.0, "#6d8175");
        sky.addColorStop(0.44, "#232c26");
        sky.addColorStop(0.6, "#26522f"); // pantulan hijau merek dari bawah
        sky.addColorStop(1.0, "#0d110f");
        g.fillStyle = sky;
        g.fillRect(0, 0, 128, 64);
        const key = g.createRadialGradient(34, 15, 1, 34, 15, 30);
        key.addColorStop(0, "#ffffff");
        key.addColorStop(1, "rgba(238,244,234,0)");
        g.fillStyle = key;
        g.fillRect(0, 0, 128, 64);
        const rim = g.createRadialGradient(104, 40, 1, 104, 40, 22);
        rim.addColorStop(0, "rgba(30,168,58,.85)");
        rim.addColorStop(1, "rgba(30,168,58,0)");
        g.fillStyle = rim;
        g.fillRect(0, 0, 128, 64);
        const t = new THREE.CanvasTexture(c);
        t.mapping = THREE.EquirectangularReflectionMapping;
        t.colorSpace = THREE.SRGBColorSpace;
        return t;
      };

      const pmrem = new THREE.PMREMGenerator(renderer);
      const src = envSource();
      const envRT = pmrem.fromEquirectangular(src);
      scene.environment = envRT.texture;
      // Material metalness .92 hampir seluruhnya dihidupi PANTULAN, bukan cahaya
      // langsung — ini tuas paling menentukan apakah baut terlihat baja atau
      // gumpalan hitam.
      // ⛔ three modern mengonversi warna hex sRGB → linier (ColorManagement aktif
      // sejak r152), jadi angka yang pas di r128 menghasilkan medan jauh lebih
      // gelap di sini. Ini kompensasinya, bukan sekadar "biar terang".
      scene.environmentIntensity = 3.0;
      src.dispose();
      pmrem.dispose();

      // Cahaya ARAH saja: intensitasnya tak bergantung jarak, jadi hasilnya sama
      // di semua perangkat tanpa perlu menyetel decay.
      // ⛔ Angka di bawah berlaku untuk pencahayaan FISIK (baku sejak three r155).
      // Nilai gaya lama (ambient .55 / key 1.35) membuat seluruh medan gelap
      // pekat — terbukti saat porting purwarupa r128 ini ke three 0.180.
      scene.add(new THREE.AmbientLight(0x33503f, 1.5));
      const key = new THREE.DirectionalLight(0xf2f7ef, 3.4);
      key.position.set(-6, 8, 9);
      const rim = new THREE.DirectionalLight(0x1ea83a, 2.4);
      rim.position.set(9, -6, -4);
      scene.add(key, rim);

      // ── Bentuk: mur heksagon berlubang, baut (kepala + batang), ring ──
      const hex = (r: number) => {
        const s = new THREE.Shape();
        for (let i = 0; i < 6; i++) {
          const a = Math.PI / 6 + (i * Math.PI) / 3;
          const x = Math.cos(a) * r;
          const y = Math.sin(a) * r;
          if (i === 0) s.moveTo(x, y);
          else s.lineTo(x, y);
        }
        s.closePath();
        return s;
      };

      const nutShape = hex(1);
      const bore = new THREE.Path();
      bore.absarc(0, 0, 0.55, 0, Math.PI * 2, true);
      nutShape.holes.push(bore);

      const bevel = { bevelEnabled: true, bevelSize: 0.05, bevelThickness: 0.05, bevelSegments: 2 };
      const geoNut = new THREE.ExtrudeGeometry(nutShape, { depth: 0.62, curveSegments: 22, ...bevel });
      const geoHead = new THREE.ExtrudeGeometry(hex(0.82), { depth: 0.48, curveSegments: 6, ...bevel });
      const geoShaft = new THREE.CylinderGeometry(0.36, 0.36, 2.3, 18);
      const geoWasher = new THREE.TorusGeometry(0.82, 0.19, 10, 30);
      geoNut.center();
      geoHead.center();

      const matSteel = new THREE.MeshStandardMaterial({
        color: 0xb9c0c9,
        metalness: 0.9,
        roughness: 0.29,
      });
      // Hijau merek dipakai HEMAT (±1 dari 5 part). Sebagian besar kesan hijau
      // sudah datang dari rim light & pantulan lingkungan, bukan dari cat.
      const matBrand = new THREE.MeshStandardMaterial({
        color: 0x1ea83a,
        metalness: 0.45,
        roughness: 0.36,
      });

      // Acak BERBENIH: susunannya sama tiap kali halaman dibuka, jadi tampilan
      // login tidak "berubah wajah" tiap muat.
      let seed = 20260907;
      const rnd = () => {
        seed = (seed * 1664525 + 1013904223) % 4294967296;
        return seed / 4294967296;
      };

      const area = host.clientWidth * host.clientHeight || 480000;
      const count = Math.max(26, Math.min(90, Math.round(area / 9000)));

      type Item = {
        obj: import("three").Object3D;
        spin: import("three").Vector3;
        rise: number;
        sway: number;
        phase: number;
      };
      const items: Item[] = [];
      const field = new THREE.Group();

      for (let i = 0; i < count; i++) {
        const kind = rnd();
        const brand = rnd() < 0.2;
        const mat = brand ? matBrand : matSteel;

        let obj: import("three").Object3D;
        if (kind < 0.42) {
          obj = new THREE.Mesh(geoNut, mat);
        } else if (kind < 0.74) {
          const g = new THREE.Group();
          const head = new THREE.Mesh(geoHead, mat);
          const shaft = new THREE.Mesh(geoShaft, mat);
          shaft.rotation.x = Math.PI / 2; // batang mengikuti sumbu Z kepala
          shaft.position.z = 1.4;
          g.add(head, shaft);
          obj = g;
        } else {
          obj = new THREE.Mesh(geoWasher, mat);
        }

        const scale = 0.34 + rnd() * 0.72;
        obj.scale.setScalar(scale);
        obj.position.set(
          (rnd() * 2 - 1) * BOUND.x,
          (rnd() * 2 - 1) * BOUND.y,
          BOUND.zFar + rnd() * (BOUND.zNear - BOUND.zFar),
        );
        obj.rotation.set(rnd() * 6.28, rnd() * 6.28, rnd() * 6.28);
        field.add(obj);

        items.push({
          obj,
          spin: new THREE.Vector3(
            (rnd() - 0.5) * 0.34,
            (rnd() - 0.5) * 0.34,
            (rnd() - 0.5) * 0.24,
          ),
          // Yang lebih besar terasa lebih dekat → hanyut lebih cepat.
          rise: (0.22 + rnd() * 0.3) * (0.5 + scale),
          sway: (rnd() - 0.5) * 0.16,
          phase: rnd() * 6.28,
        });
      }
      scene.add(field);

      // ── Paralaks kursor (di HP tak ada pointer melayang → medan diam saja) ──
      const aim = { x: 0, y: 0 };
      const eye = { x: 0, y: 0 };
      const onMove = (e: PointerEvent) => {
        if (e.pointerType !== "mouse") return;
        const r = host.getBoundingClientRect();
        aim.x = ((e.clientX - r.left) / r.width) * 2 - 1;
        aim.y = ((e.clientY - r.top) / r.height) * 2 - 1;
      };
      const onLeave = () => {
        aim.x = 0;
        aim.y = 0;
      };
      host.addEventListener("pointermove", onMove);
      host.addEventListener("pointerleave", onLeave);

      let clock = 0;

      const draw = (dt: number) => {
        eye.x += (aim.x - eye.x) * 0.055;
        eye.y += (aim.y - eye.y) * 0.055;
        field.rotation.y = -eye.x * 0.13;
        field.rotation.x = eye.y * 0.09;
        camera.position.x = eye.x * 0.9;
        camera.position.y = -eye.y * 0.6;
        camera.lookAt(0, 0, 0);

        if (dt > 0) {
          clock += dt;
          for (const it of items) {
            const o = it.obj;
            o.rotation.x += it.spin.x * dt;
            o.rotation.y += it.spin.y * dt;
            o.rotation.z += it.spin.z * dt;
            o.position.y += it.rise * dt;
            o.position.x += Math.sin(clock * 0.5 + it.phase) * it.sway * dt;
            if (o.position.y > BOUND.y) {
              // keluar lewat atas → masuk lagi dari bawah, kolom acak
              o.position.y = -BOUND.y;
              o.position.x = (rnd() * 2 - 1) * BOUND.x;
            }
          }
        }

        renderer.render(scene, camera);
      };

      let raf = 0;
      let last = 0;
      let playing = false;

      const stop = () => {
        playing = false;
        if (raf) cancelAnimationFrame(raf);
        raf = 0;
        last = 0;
      };

      const frame = (now: number) => {
        raf = requestAnimationFrame(frame);
        const dt = last ? Math.min((now - last) / 1000, 0.05) : 0;
        last = now;
        draw(dt);
      };

      const reduce = window.matchMedia("(prefers-reduced-motion: reduce)");

      const resume = () => {
        stop();
        // Diam = SATU bingkai lalu rAF berhenti sama sekali: nol GPU, nol baterai.
        if (reduce.matches || document.hidden) {
          draw(0);
          return;
        }
        playing = true;
        raf = requestAnimationFrame(frame);
      };

      const resize = () => {
        const w = host.clientWidth || 1;
        const h = host.clientHeight || 1;
        // Layar HP ber-dpr 3 = 9× piksel untuk latar yang cuma dekoratif.
        const cap = w < 768 ? 1.5 : 2;
        renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, cap));
        renderer.setSize(w, h, false);
        camera.aspect = w / h;
        camera.updateProjectionMatrix();
        if (!playing) draw(0);
      };

      const ro = new ResizeObserver(resize);
      ro.observe(host);
      reduce.addEventListener("change", resume);
      document.addEventListener("visibilitychange", resume);

      resize();
      resume();

      teardown = () => {
        stop();
        ro.disconnect();
        reduce.removeEventListener("change", resume);
        document.removeEventListener("visibilitychange", resume);
        host.removeEventListener("pointermove", onMove);
        host.removeEventListener("pointerleave", onLeave);
        [geoNut, geoHead, geoShaft, geoWasher].forEach((g) => g.dispose());
        [matSteel, matBrand].forEach((m) => m.dispose());
        envRT.dispose();
        renderer.dispose();
      };
    })().catch(() => {
      /* three.js gagal dimuat → latar CSS tetap tampil, login tetap jalan */
    });

    return () => {
      dead = true;
      teardown();
    };
  }, []);

  return (
    <>
      <div className="login-fallback" aria-hidden="true" />
      <canvas ref={canvasRef} className="login-canvas" aria-hidden="true" />
      {/* Peredup: teks panel harus tetap terbaca di atas medan yang bergerak.
          Sengaja TANPA z-index — urutan DOM yang menaruhnya di bawah isi panel. */}
      <div className="login-scrim" aria-hidden="true" />
    </>
  );
}
