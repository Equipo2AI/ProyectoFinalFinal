"""Ajustes de color del tema claro sobre el CSS base.

El CSS base vive en ``estilos.py``. Este módulo solo guarda los
fragmentos que cambian entre el tema oscuro y el claro; así no hay dos
hojas de estilo completas que mantener en paralelo.
"""

from __future__ import annotations

AJUSTES_CLARO: tuple[tuple[str, str], ...] = (
    (
        '  --papel: #121417;\n  --papel-2: #171A1E;\n  --tinta: #F2EEE8;\n  --tinta-2: #DED8D0;\n  --cuerpo: #C2BDB5;\n  --tenue: #9E9991;\n  --plata: #7C7871;\n  --rojo: #E23B45;\n  --teal: #6FB7A8;\n  --ambar: #D8A45C;\n  --inverso: #16191C;',
        '  --papel: #F3EDE4;\n  --papel-2: #FAF7F1;\n  --tinta: #171923;\n  --tinta-2: #2A2622;\n  --cuerpo: #4A463F;\n  --tenue: #625D55;\n  --plata: #70685E;\n  --rojo: #C8102E;\n  --teal: #2F7F73;\n  --ambar: #A96F1F;\n  --inverso: #F8F4EC;',
    ),
    (
        '  --borde: rgba(236, 240, 244, 0.15);\n  --borde-suave: rgba(236, 240, 244, 0.085);\n  --cristal: rgba(26, 30, 34, 0.68);',
        '  --borde: rgba(23, 25, 35, 0.10);\n  --borde-suave: rgba(23, 25, 35, 0.07);\n  --cristal: rgba(255, 255, 255, 0.54);',
    ),
    (
        '  --ico-enviar: url("data:image/svg+xml;utf8,<svg xmlns=\'http://www.w3.org/2000/svg\' viewBox=\'0 0 24 24\' fill=\'none\' stroke=\'%23FFFFFF\' stroke-width=\'1.5\' stroke-linecap=\'round\' stroke-linejoin=\'round\'><path d=\'m22 2-7 20-4-9-9-4Z\'/><path d=\'M22 2 11 13\'/></svg>");\n  --ico-user: url("data:image/svg+xml;utf8,<svg xmlns=\'http://www.w3.org/2000/svg\' viewBox=\'0 0 24 24\' fill=\'none\' stroke=\'%2316191C\' stroke-width=\'1.5\' stroke-linecap=\'round\' stroke-linejoin=\'round\'><path d=\'M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2\'/><circle cx=\'12\' cy=\'7\' r=\'4\'/></svg>");\n  --ico-marca: url("data:image/svg+xml;utf8,<svg xmlns=\'http://www.w3.org/2000/svg\' viewBox=\'0 0 24 24\' fill=\'none\' stroke=\'%23F2EEE8\' stroke-width=\'1.4\' stroke-linecap=\'round\'><circle cx=\'12\' cy=\'12\' r=\'8.6\'/><circle cx=\'12\' cy=\'12\' r=\'2.4\' fill=\'%23E23B45\' stroke=\'none\'/></svg>");\n  --ico-chart: url("data:image/svg+xml;utf8,<svg xmlns=\'http://www.w3.org/2000/svg\' viewBox=\'0 0 24 24\' fill=\'none\' stroke=\'%239E9991\' stroke-width=\'1.5\' stroke-linecap=\'round\' stroke-linejoin=\'round\'><path d=\'M3 3v18h18\'/><path d=\'M8 17V9\'/><path d=\'M13 17V5\'/><path d=\'M18 17v-5\'/></svg>");\n  --ico-db: url("data:image/svg+xml;utf8,<svg xmlns=\'http://www.w3.org/2000/svg\' viewBox=\'0 0 24 24\' fill=\'none\' stroke=\'%239E9991\' stroke-width=\'1.5\' stroke-linecap=\'round\' stroke-linejoin=\'round\'><ellipse cx=\'12\' cy=\'5\' rx=\'9\' ry=\'3\'/><path d=\'M3 5v14a9 3 0 0 0 18 0V5\'/><path d=\'M3 12a9 3 0 0 0 18 0\'/></svg>");\n  --ico-book: url("data:image/svg+xml;utf8,<svg xmlns=\'http://www.w3.org/2000/svg\' viewBox=\'0 0 24 24\' fill=\'none\' stroke=\'%239E9991\' stroke-width=\'1.5\' stroke-linecap=\'round\' stroke-linejoin=\'round\'><path d=\'M12 7v14\'/><path d=\'M3 18a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h5a4 4 0 0 1 4 4 4 4 0 0 1 4-4h5a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1h-6a3 3 0 0 0-3 3 3 3 0 0 0-3-3z\'/></svg>");\n  --ico-cerrar: url("data:image/svg+xml;utf8,<svg xmlns=\'http://www.w3.org/2000/svg\' viewBox=\'0 0 24 24\' fill=\'none\' stroke=\'%23F2EEE8\' stroke-width=\'1.5\' stroke-linecap=\'round\' stroke-linejoin=\'round\'><rect width=\'18\' height=\'18\' x=\'3\' y=\'3\' rx=\'2\'/><path d=\'M15 3v18\'/><path d=\'m8 9 3 3-3 3\'/></svg>");\n  --ico-abrir: url("data:image/svg+xml;utf8,<svg xmlns=\'http://www.w3.org/2000/svg\' viewBox=\'0 0 24 24\' fill=\'none\' stroke=\'%23F2EEE8\' stroke-width=\'1.5\' stroke-linecap=\'round\' stroke-linejoin=\'round\'><rect width=\'18\' height=\'18\' x=\'3\' y=\'3\' rx=\'2\'/><path d=\'M15 3v18\'/><path d=\'m10 15-3-3 3-3\'/></svg>");\n  --ico-tema: url("data:image/svg+xml;utf8,<svg xmlns=\'http://www.w3.org/2000/svg\' viewBox=\'0 0 24 24\' fill=\'none\' stroke=\'%23F2EEE8\' stroke-width=\'1.5\' stroke-linecap=\'round\' stroke-linejoin=\'round\'><circle cx=\'12\' cy=\'12\' r=\'4\'/><path d=\'M12 2v2\'/><path d=\'M12 20v2\'/><path d=\'m4.93 4.93 1.41 1.41\'/><path d=\'m17.66 17.66 1.41 1.41\'/><path d=\'M2 12h2\'/><path d=\'M20 12h2\'/><path d=\'m6.34 17.66-1.41 1.41\'/><path d=\'m19.07 4.93-1.41 1.41\'/></svg>");',
        '  --ico-enviar: url("data:image/svg+xml;utf8,<svg xmlns=\'http://www.w3.org/2000/svg\' viewBox=\'0 0 24 24\' fill=\'none\' stroke=\'%23FFFFFF\' stroke-width=\'1.5\' stroke-linecap=\'round\' stroke-linejoin=\'round\'><path d=\'m5 12 7-7 7 7\'/><path d=\'M12 19V5\'/></svg>");\n  --ico-user: url("data:image/svg+xml;utf8,<svg xmlns=\'http://www.w3.org/2000/svg\' viewBox=\'0 0 24 24\' fill=\'none\' stroke=\'%23F8F4EC\' stroke-width=\'1.5\' stroke-linecap=\'round\' stroke-linejoin=\'round\'><path d=\'M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2\'/><circle cx=\'12\' cy=\'7\' r=\'4\'/></svg>");\n  --ico-marca: url("data:image/svg+xml;utf8,<svg xmlns=\'http://www.w3.org/2000/svg\' viewBox=\'0 0 24 24\' fill=\'none\' stroke=\'%23171923\' stroke-width=\'1.4\' stroke-linecap=\'round\'><circle cx=\'12\' cy=\'12\' r=\'8.6\'/><circle cx=\'12\' cy=\'12\' r=\'2.4\' fill=\'%23C8102E\' stroke=\'none\'/></svg>");\n  --ico-chart: url("data:image/svg+xml;utf8,<svg xmlns=\'http://www.w3.org/2000/svg\' viewBox=\'0 0 24 24\' fill=\'none\' stroke=\'%237A746B\' stroke-width=\'1.5\' stroke-linecap=\'round\' stroke-linejoin=\'round\'><path d=\'M3 3v18h18\'/><path d=\'M8 17V9\'/><path d=\'M13 17V5\'/><path d=\'M18 17v-5\'/></svg>");\n  --ico-db: url("data:image/svg+xml;utf8,<svg xmlns=\'http://www.w3.org/2000/svg\' viewBox=\'0 0 24 24\' fill=\'none\' stroke=\'%237A746B\' stroke-width=\'1.5\' stroke-linecap=\'round\' stroke-linejoin=\'round\'><ellipse cx=\'12\' cy=\'5\' rx=\'9\' ry=\'3\'/><path d=\'M3 5v14a9 3 0 0 0 18 0V5\'/><path d=\'M3 12a9 3 0 0 0 18 0\'/></svg>");\n  --ico-book: url("data:image/svg+xml;utf8,<svg xmlns=\'http://www.w3.org/2000/svg\' viewBox=\'0 0 24 24\' fill=\'none\' stroke=\'%237A746B\' stroke-width=\'1.5\' stroke-linecap=\'round\' stroke-linejoin=\'round\'><path d=\'M12 7v14\'/><path d=\'M3 18a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h5a4 4 0 0 1 4 4 4 4 0 0 1 4-4h5a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1h-6a3 3 0 0 0-3 3 3 3 0 0 0-3-3z\'/></svg>");\n  --ico-cerrar: url("data:image/svg+xml;utf8,<svg xmlns=\'http://www.w3.org/2000/svg\' viewBox=\'0 0 24 24\' fill=\'none\' stroke=\'%23171923\' stroke-width=\'1.5\' stroke-linecap=\'round\' stroke-linejoin=\'round\'><rect width=\'18\' height=\'18\' x=\'3\' y=\'3\' rx=\'2\'/><path d=\'M15 3v18\'/><path d=\'m8 9 3 3-3 3\'/></svg>");\n  --ico-abrir: url("data:image/svg+xml;utf8,<svg xmlns=\'http://www.w3.org/2000/svg\' viewBox=\'0 0 24 24\' fill=\'none\' stroke=\'%23171923\' stroke-width=\'1.5\' stroke-linecap=\'round\' stroke-linejoin=\'round\'><rect width=\'18\' height=\'18\' x=\'3\' y=\'3\' rx=\'2\'/><path d=\'M15 3v18\'/><path d=\'m10 15-3-3 3-3\'/></svg>");\n  --ico-tema: url("data:image/svg+xml;utf8,<svg xmlns=\'http://www.w3.org/2000/svg\' viewBox=\'0 0 24 24\' fill=\'none\' stroke=\'%23171923\' stroke-width=\'1.5\' stroke-linecap=\'round\' stroke-linejoin=\'round\'><path d=\'M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z\'/></svg>");',
    ),
    (
        '    radial-gradient(38vw 38vw at 1% 0%, rgba(226, 59, 69, 0.14) 0%, rgba(226, 59, 69, 0) 68%),\n    radial-gradient(34vw 34vw at 100% 10%, rgba(90, 120, 170, 0.10) 0%, rgba(90, 120, 170, 0) 70%),\n    radial-gradient(38vw 38vw at 92% 102%, rgba(90, 165, 150, 0.10) 0%, rgba(90, 165, 150, 0) 70%),\n    radial-gradient(32vw 32vw at 8% 104%, rgba(180, 140, 90, 0.07) 0%, rgba(180, 140, 90, 0) 70%);',
        '    radial-gradient(38vw 38vw at 1% 0%, rgba(232, 103, 90, 0.20) 0%, rgba(232, 103, 90, 0) 68%),\n    radial-gradient(34vw 34vw at 100% 10%, rgba(188, 140, 180, 0.14) 0%, rgba(188, 140, 180, 0) 70%),\n    radial-gradient(38vw 38vw at 92% 102%, rgba(105, 172, 160, 0.11) 0%, rgba(105, 172, 160, 0) 70%),\n    radial-gradient(32vw 32vw at 8% 104%, rgba(225, 175, 115, 0.14) 0%, rgba(225, 175, 115, 0) 70%);',
    ),
    (
        '  opacity: 0.045;',
        '  opacity: 0.035;',
    ),
    (
        '  mix-blend-mode: soft-light;',
        '  mix-blend-mode: multiply;',
    ),
    (
        '  background: rgba(236, 240, 244, 0.07);\n  color: var(--tinta);',
        '  background: rgba(255, 255, 255, 0.55);\n  color: var(--tinta);',
    ),
    (
        '  box-shadow: 0 6px 18px rgba(0, 0, 0, 0.35), inset 0 1px 0 rgba(236, 240, 244, 0.06);',
        '  box-shadow: 0 6px 18px rgba(70, 45, 30, 0.06), inset 0 1px 0 rgba(255, 255, 255, 0.9);',
    ),
    (
        '    0 38px 90px -52px rgba(0, 0, 0, 0.85),\n    0 8px 26px rgba(0, 0, 0, 0.28),\n    inset 0 1px 0 rgba(236, 240, 244, 0.06);',
        '    0 38px 90px -52px rgba(70, 45, 30, 0.45),\n    0 8px 26px rgba(70, 45, 30, 0.05),\n    inset 0 1px 0 rgba(255, 255, 255, 0.9);',
    ),
    (
        '    rgba(236, 240, 244, 0.24) 0%,\n    rgba(242, 121, 109, 0.32) 38%,\n    rgba(111, 183, 168, 0.22) 66%,\n    rgba(236, 240, 244, 0.12) 100%);',
        '    rgba(255, 255, 255, 0.95) 0%,\n    rgba(232, 103, 90, 0.16) 38%,\n    rgba(105, 172, 160, 0.10) 66%,\n    rgba(255, 255, 255, 0.75) 100%);',
    ),
    (
        '  border-color: rgba(242, 121, 109, 0.30) !important;\n  background: rgba(242, 121, 109, 0.08) !important;',
        '  border-color: rgba(200, 16, 46, 0.16) !important;\n  background: rgba(200, 16, 46, 0.04) !important;',
    ),
    (
        '    rgba(226, 59, 69, 0.18) 0%,\n    rgba(216, 164, 92, 0.07) 44%,\n    rgba(226, 59, 69, 0) 72%);',
        '    rgba(232, 103, 90, 0.18) 0%,\n    rgba(225, 175, 115, 0.09) 44%,\n    rgba(232, 103, 90, 0) 72%);',
    ),
    (
        '  background-color: rgba(236, 240, 244, 0.045) !important;',
        '  background-color: rgba(255, 255, 255, 0.40) !important;',
    ),
    (
        '  box-shadow: 0 14px 34px -26px rgba(0, 0, 0, 0.55), inset 0 1px 0 rgba(236, 240, 244, 0.05);',
        '  box-shadow: 0 14px 34px -26px rgba(70, 45, 30, 0.30), inset 0 1px 0 rgba(255, 255, 255, 0.8);',
    ),
    (
        '  background-color: rgba(236, 240, 244, 0.065) !important;\n  border-color: rgba(242, 121, 109, 0.38) !important;',
        '  background-color: rgba(255, 255, 255, 0.70) !important;\n  border-color: rgba(200, 16, 46, 0.24) !important;',
    ),
    (
        '  box-shadow: 0 22px 44px -28px rgba(0, 0, 0, 0.65), inset 0 1px 0 rgba(236, 240, 244, 0.07);',
        '  box-shadow: 0 22px 44px -28px rgba(70, 45, 30, 0.34), inset 0 1px 0 rgba(255, 255, 255, 0.9);',
    ),
    (
        '  background: rgba(34, 38, 43, 0.72);',
        '  background: rgba(255, 255, 255, 0.62);',
    ),
    (
        '  box-shadow: 0 18px 40px -30px rgba(0, 0, 0, 0.65), inset 0 1px 0 rgba(236, 240, 244, 0.05);',
        '  box-shadow: 0 18px 40px -30px rgba(70, 45, 30, 0.34), inset 0 1px 0 rgba(255, 255, 255, 0.85);',
    ),
    (
        '  box-shadow: 0 18px 40px -30px rgba(0, 0, 0, 0.65);',
        '  box-shadow: 0 18px 40px -30px rgba(23, 25, 35, 0.55);',
    ),
    (
        '  color: var(--inverso);',
        '  color: #F8F4EC;',
    ),
    (
        '  background-color: rgba(236, 240, 244, 0.07);',
        '  background-color: rgba(255, 255, 255, 0.70);',
    ),
    (
        '  box-shadow: 0 4px 14px rgba(0, 0, 0, 0.35);',
        '  box-shadow: 0 4px 14px rgba(70, 45, 30, 0.08);',
    ),
    (
        '  color: var(--inverso) !important;',
        '  color: #F8F4EC !important;',
    ),
    (
        '  background: rgba(236, 240, 244, 0.07);\n  color: var(--rojo);',
        '  background: rgba(255, 255, 255, 0.55);\n  color: var(--rojo);',
    ),
    (
        '  background: rgba(42, 46, 52, 0.88) !important;',
        '  background: rgba(255, 255, 255, 0.72) !important;',
    ),
    (
        '  box-shadow: 0 18px 44px -32px rgba(0, 0, 0, 0.65), inset 0 1px 0 rgba(236, 240, 244, 0.05);',
        '  box-shadow: 0 18px 44px -32px rgba(70, 45, 30, 0.42), inset 0 1px 0 rgba(255, 255, 255, 0.9);',
    ),
    (
        '  border-color: rgba(242, 121, 109, 0.45) !important;',
        '  border-color: rgba(200, 16, 46, 0.32) !important;',
    ),
    (
        '    0 0 0 3px rgba(242, 121, 109, 0.12),\n    0 18px 44px -32px rgba(0, 0, 0, 0.65),\n    inset 0 1px 0 rgba(236, 240, 244, 0.07);',
        '    0 0 0 3px rgba(200, 16, 46, 0.06),\n    0 18px 44px -32px rgba(70, 45, 30, 0.42),\n    inset 0 1px 0 rgba(255, 255, 255, 0.95);',
    ),
    (
        '  color: rgba(156, 144, 130, 0.78);',
        '  color: #70685E;',
    ),
    (
        '  background-color: #C22B36 !important;',
        '  background-color: #A60D26 !important;',
    ),
    (
        '  background-color: rgba(236, 240, 244, 0.06) !important;',
        '  background-color: rgba(255, 255, 255, 0.55) !important;',
    ),
    (
        '  box-shadow: 0 6px 18px rgba(0, 0, 0, 0.35);',
        '  box-shadow: 0 6px 18px rgba(70, 45, 30, 0.06);',
    ),
    (
        '  background-color: rgba(236, 240, 244, 0.08) !important;\n  border-color: rgba(236, 240, 244, 0.20) !important;',
        '  background-color: rgba(255, 255, 255, 0.85) !important;\n  border-color: rgba(23, 25, 35, 0.20) !important;',
    ),
    (
        '  background: rgba(236, 240, 244, 0.045);\n  font-size: 8px;',
        '  background: rgba(255, 255, 255, 0.40);\n  font-size: 8px;',
    ),
    (
        '.estado.ok { color: #8BCBBE; border-color: rgba(111, 183, 168, 0.30); }\n.estado.ok .dot { background: var(--teal); box-shadow: 0 0 0 3px rgba(111, 183, 168, 0.18); }\n.estado.pensando { color: #E4C28F; border-color: rgba(216, 164, 92, 0.30); }',
        '.estado.ok { color: #246B61; border-color: rgba(47, 127, 115, 0.18); }\n.estado.ok .dot { background: var(--teal); box-shadow: 0 0 0 3px rgba(47, 127, 115, 0.10); }\n.estado.pensando { color: #8A5A16; border-color: rgba(169, 111, 31, 0.20); }',
    ),
    (
        '.estado.error { color: #F08A92; border-color: rgba(226, 59, 69, 0.30); }',
        '.estado.error { color: #8A1B2E; border-color: rgba(200, 16, 46, 0.20); }',
    ),
    (
        '  background: linear-gradient(180deg, rgba(226, 59, 69, 0.38) 0%, rgba(236, 240, 244, 0.10) 100%);',
        '  background: linear-gradient(180deg, rgba(200, 16, 46, 0.28) 0%, rgba(23, 25, 35, 0.10) 100%);',
    ),
    (
        '  background: rgba(236, 240, 244, 0.07);\n  border: 1px solid var(--borde);',
        '  background: rgba(255, 255, 255, 0.66);\n  border: 1px solid var(--borde);',
    ),
    (
        '  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.35), inset 0 1px 0 rgba(236, 240, 244, 0.06);',
        '  box-shadow: 0 4px 12px rgba(70, 45, 30, 0.05), inset 0 1px 0 rgba(255, 255, 255, 0.9);',
    ),
    (
        '  box-shadow: 0 0 0 2px rgba(111, 183, 168, 0.18);',
        '  box-shadow: 0 0 0 2px rgba(47, 127, 115, 0.10);',
    ),
    (
        '  border-color: rgba(216, 164, 92, 0.38);\n  color: #E4C28F;',
        '  border-color: rgba(169, 111, 31, 0.28);\n  color: #8A5A16;',
    ),
    (
        '.paso[data-estado="alerta"] .nodo::after { background: var(--ambar); box-shadow: 0 0 0 2px rgba(216, 164, 92, 0.18); }',
        '.paso[data-estado="alerta"] .nodo::after { background: var(--ambar); box-shadow: 0 0 0 2px rgba(169, 111, 31, 0.10); }',
    ),
    (
        '  border-color: rgba(226, 59, 69, 0.38);\n  color: #F08A92;',
        '  border-color: rgba(200, 16, 46, 0.28);\n  color: #8A1B2E;',
    ),
    (
        '.paso[data-estado="error"] .nodo::after { background: var(--rojo); box-shadow: 0 0 0 2px rgba(226, 59, 69, 0.18); }',
        '.paso[data-estado="error"] .nodo::after { background: var(--rojo); box-shadow: 0 0 0 2px rgba(200, 16, 46, 0.10); }',
    ),
    (
        '  border-color: rgba(226, 59, 69, 0.35);\n  box-shadow: 0 0 0 4px rgba(226, 59, 69, 0.08), inset 0 1px 0 rgba(236, 240, 244, 0.06);',
        '  border-color: rgba(200, 16, 46, 0.22);\n  box-shadow: 0 0 0 4px rgba(200, 16, 46, 0.04), inset 0 1px 0 rgba(255, 255, 255, 0.9);',
    ),
    (
        '  background: rgba(216, 164, 92, 0.10);\n  border: 1px solid rgba(216, 164, 92, 0.30);\n  color: #D8B37C;',
        '  background: rgba(214, 158, 46, 0.09);\n  border: 1px solid rgba(169, 111, 31, 0.20);\n  color: #6B4A16;',
    ),
    (
        '  color: #E4C28F;\n  margin-bottom: 3px;',
        '  color: #8A5A16;\n  margin-bottom: 3px;',
    ),
    (
        '  line-height: 1.55;\n  color: #D8B37C;',
        '  line-height: 1.55;\n  color: #6B4A16;',
    ),
    (
        '  background: rgba(226, 59, 69, 0.09);\n  border-color: rgba(226, 59, 69, 0.28);\n  color: #F08A92;',
        '  background: rgba(200, 16, 46, 0.05);\n  border-color: rgba(200, 16, 46, 0.18);\n  color: #8A1B2E;',
    ),
    (
        '.aviso.error span { color: #F08A92; }',
        '.aviso.error span { color: #8A1B2E; }',
    ),
    (
        '  background: rgba(236, 240, 244, 0.045) !important;',
        '  background: rgba(255, 255, 255, 0.40) !important;',
    ),
    (
        '}\n[data-testid="stExpander"] summary p {',
        '  background: transparent !important;\n}\n[data-testid="stExpander"] summary svg {\n  color: var(--tenue) !important;\n  fill: var(--tenue) !important;\n}\n[data-testid="stExpander"] summary p {',
    ),
    (
        '  box-shadow: inset 0 1px 0 rgba(236, 240, 244, 0.05);',
        '  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.8);',
    ),
    (
        '  background: #15181B !important;',
        '  background: #FBF8F2 !important;',
    ),
    (
        '[data-testid="stCode"] code {',
        '[data-testid="stCode"] code,\n[data-testid="stCode"] .token {',
    ),
    (
        '  line-height: 1.65 !important;',
        '  line-height: 1.65 !important;\n  color: #2A2622 !important;',
    ),
    (
        '\n[data-testid="stDataFrame"] {',
        '[data-testid="stCode"] .token.keyword { color: #8A1B2E !important; }\n[data-testid="stCode"] .token.function { color: #2F7F73 !important; }\n[data-testid="stCode"] .token.string { color: #8A5A16 !important; }\n[data-testid="stCode"] .token.number { color: #4C5B8A !important; }\n[data-testid="stCode"] .token.operator { color: #C8102E !important; }\n[data-testid="stCode"] .token.punctuation { color: #6A645C !important; }\n[data-testid="stCode"] .token.comment { color: #7A746B !important; }\n\n[data-testid="stDataFrame"] {',
    ),
    (
        '  background: rgba(236, 240, 244, 0.035);\n  box-shadow: inset 0 1px 0 rgba(236, 240, 244, 0.04);',
        '  background: rgba(255, 255, 255, 0.30);\n  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.75);',
    ),
    (
        '  background: rgba(236, 240, 244, 0.07);\n  border-bottom: 1px solid var(--borde);',
        '  background: rgba(239, 233, 223, 0.64);\n  border-bottom: 1px solid var(--borde);',
    ),
    (
        '.tabla-datos tbody tr:nth-child(even) { background: rgba(236, 240, 244, 0.035); }\n.tabla-datos tbody tr:hover { background: rgba(226, 59, 69, 0.07); }',
        '.tabla-datos tbody tr:nth-child(even) { background: rgba(23, 25, 35, 0.018); }\n.tabla-datos tbody tr:hover { background: rgba(200, 16, 46, 0.035); }',
    ),
    (
        '  border: 1px solid rgba(226, 59, 69, 0.28);',
        '  border: 1px solid rgba(200, 16, 46, 0.12);',
    ),
    (
        '  background: rgba(226, 59, 69, 0.09);\n  color: #F08A92;',
        '  background: rgba(200, 16, 46, 0.055);\n  color: #8A1B2E;',
    ),
    (
        '  box-shadow: 0 20px 50px -32px rgba(0, 0, 0, 0.75);',
        '  box-shadow: 0 20px 50px -32px rgba(70, 45, 30, 0.35);',
    ),
    (
        '  background: rgba(236, 240, 244, 0.045);\n  font-size: 10px;',
        '  background: rgba(255, 255, 255, 0.38);\n  font-size: 10px;',
    ),
    (
        '    rgba(226, 59, 69, 0) 0%,\n    rgba(226, 59, 69, 0.65) 34%,\n    rgba(242, 121, 109, 0.95) 50%,\n    rgba(226, 59, 69, 0.55) 66%,\n    rgba(226, 59, 69, 0) 100%);',
        '    rgba(200, 16, 46, 0) 0%,\n    rgba(200, 16, 46, 0.55) 34%,\n    rgba(232, 103, 90, 0.85) 50%,\n    rgba(200, 16, 46, 0.45) 66%,\n    rgba(200, 16, 46, 0) 100%);',
    ),
    (
        '::-webkit-scrollbar-thumb { background: rgba(236, 240, 244, 0.14); border-radius: 999px; }\n::-webkit-scrollbar-thumb:hover { background: rgba(236, 240, 244, 0.24); }',
        '::-webkit-scrollbar-thumb { background: rgba(23, 25, 35, 0.16); border-radius: 999px; }\n::-webkit-scrollbar-thumb:hover { background: rgba(23, 25, 35, 0.28); }',
    ),
    (
        '</style>',
        '\n[data-testid="stElementToolbarButtonContainer"] {\n  background-color: rgba(255, 255, 255, 0.86) !important;\n  border: 1px solid var(--borde) !important;\n  box-shadow: 0 8px 20px -14px rgba(70, 45, 30, 0.35) !important;\n}\n[data-testid="stBaseButton-elementToolbar"] { color: var(--tinta) !important; }\n[data-testid="stBaseButton-elementToolbar"] svg { fill: var(--tinta) !important; }\n[data-testid="stCodeCopyButton"] { color: var(--tenue) !important; }\n[data-testid="stCodeCopyButton"] svg { fill: var(--tenue) !important; }\n</style>',
    ),
)


def aplicar_ajustes(css: str) -> str:
    """Aplica el delta del tema claro al CSS oscuro."""
    for indice, (oscuro, claro) in enumerate(AJUSTES_CLARO, 1):
        if oscuro not in css:
            raise ValueError(
                f"No se encontró el ajuste {indice} en el CSS base."
            )
        css = css.replace(oscuro, claro, 1)
    return css
