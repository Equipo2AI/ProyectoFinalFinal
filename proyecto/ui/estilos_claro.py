from __future__ import annotations

import streamlit as st

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600&family=Manrope:wght@300;400;500;600;700&display=swap');

:root {
  --papel: #F3EDE4;
  --papel-2: #FAF7F1;
  --tinta: #171923;
  --tinta-2: #2A2622;
  --cuerpo: #4A463F;
  --tenue: #7A746B;
  --plata: #A39B8F;
  --rojo: #C8102E;
  --teal: #2F7F73;
  --ambar: #A96F1F;

  --borde: rgba(23, 25, 35, 0.10);
  --borde-suave: rgba(23, 25, 35, 0.07);
  --cristal: rgba(255, 255, 255, 0.54);
  --radio: 26px;

  --ico-enviar: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%23FFFFFF' stroke-width='1.5' stroke-linecap='round' stroke-linejoin='round'><path d='m5 12 7-7 7 7'/><path d='M12 19V5'/></svg>");
  --ico-user: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%23F8F4EC' stroke-width='1.5' stroke-linecap='round' stroke-linejoin='round'><path d='M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2'/><circle cx='12' cy='7' r='4'/></svg>");
  --ico-marca: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%23171923' stroke-width='1.4' stroke-linecap='round'><circle cx='12' cy='12' r='8.6'/><circle cx='12' cy='12' r='2.4' fill='%23C8102E' stroke='none'/></svg>");
  --ico-chart: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%237A746B' stroke-width='1.5' stroke-linecap='round' stroke-linejoin='round'><path d='M3 3v18h18'/><path d='M8 17V9'/><path d='M13 17V5'/><path d='M18 17v-5'/></svg>");
  --ico-db: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%237A746B' stroke-width='1.5' stroke-linecap='round' stroke-linejoin='round'><ellipse cx='12' cy='5' rx='9' ry='3'/><path d='M3 5v14a9 3 0 0 0 18 0V5'/><path d='M3 12a9 3 0 0 0 18 0'/></svg>");
  --ico-book: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%237A746B' stroke-width='1.5' stroke-linecap='round' stroke-linejoin='round'><path d='M12 7v14'/><path d='M3 18a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h5a4 4 0 0 1 4 4 4 4 0 0 1 4-4h5a1 1 0 0 1 1 1v13a1 1 0 0 1-1 1h-6a3 3 0 0 0-3 3 3 3 0 0 0-3-3z'/></svg>");
  --ico-cerrar: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%23171923' stroke-width='1.5' stroke-linecap='round' stroke-linejoin='round'><rect width='18' height='18' x='3' y='3' rx='2'/><path d='M15 3v18'/><path d='m8 9 3 3-3 3'/></svg>");
  --ico-abrir: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%23171923' stroke-width='1.5' stroke-linecap='round' stroke-linejoin='round'><rect width='18' height='18' x='3' y='3' rx='2'/><path d='M15 3v18'/><path d='m10 15-3-3 3-3'/></svg>");
  --ico-tema: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' fill='none' stroke='%23171923' stroke-width='1.5' stroke-linecap='round' stroke-linejoin='round'><path d='M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z'/></svg>");
}

html, body, .stApp, [data-testid="stAppViewContainer"],
button, input, textarea, select,
[data-testid="stMarkdownContainer"] {
  font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif !important;
  -webkit-font-smoothing: antialiased;
  text-rendering: optimizeLegibility;
}
.brand, .hero .t, .rotulo, .seccion-t, .estado,
.st-key-chat_scroll [data-testid="stHorizontalBlock"] button::before,
.hero-kicker {
  font-family: 'Manrope', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif !important;
}

html, body, .stApp { background-color: var(--papel) !important; }
[data-testid="stAppViewContainer"] {
  background-color: var(--papel);
  background-image:
    radial-gradient(38vw 38vw at 1% 0%, rgba(232, 103, 90, 0.20) 0%, rgba(232, 103, 90, 0) 68%),
    radial-gradient(34vw 34vw at 100% 10%, rgba(188, 140, 180, 0.14) 0%, rgba(188, 140, 180, 0) 70%),
    radial-gradient(38vw 38vw at 92% 102%, rgba(105, 172, 160, 0.11) 0%, rgba(105, 172, 160, 0) 70%),
    radial-gradient(32vw 32vw at 8% 104%, rgba(225, 175, 115, 0.14) 0%, rgba(225, 175, 115, 0) 70%);
  background-attachment: fixed;
}

[data-testid="stAppViewContainer"]::before {
  content: "";
  position: fixed;
  inset: 0;
  z-index: 0;
  pointer-events: none;
  opacity: 0.035;
  background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='140' height='140'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='.9' numOctaves='2' stitchTiles='stitch'/></filter><rect width='100%' height='100%' filter='url(%23n)' opacity='.55'/></svg>");
  background-size: 140px 140px;
  mix-blend-mode: multiply;
}

[data-testid="stMain"] { position: relative; z-index: 1; }

[data-testid="stHeader"] { background: transparent; pointer-events: none; }
[data-testid="stHeader"] * { pointer-events: none; }
[data-testid="stToolbar"] { display: none; }
#MainMenu, footer { visibility: hidden; }

[data-testid="stMainBlockContainer"] {
  padding: 1.35rem 2.1rem 0.2rem;
  max-width: none;
}

.st-key-topbar { margin-bottom: 16px; }
.st-key-topbar [data-testid="stHorizontalBlock"] {
  flex-wrap: nowrap !important;
  align-items: center !important;
  gap: 10px !important;
}
.st-key-topbar [data-testid="stColumn"]:first-child { min-width: 0 !important; }
.st-key-topbar [data-testid="stColumn"]:last-child {
  flex: 0 0 44px !important;
  width: 44px !important;
  min-width: 44px !important;
}
.st-key-topbar [data-testid="stColumn"]:nth-last-child(2) {
  flex: 0 0 44px !important;
  width: 44px !important;
  min-width: 44px !important;
}

.topbar {
  display: flex;
  align-items: center;
  gap: 13px;
  width: 100%;
  padding: 0 2px;
}
.marca {
  width: 36px;
  height: 36px;
  flex: none;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  border: 1px solid var(--borde);
  background: rgba(255, 255, 255, 0.55);
  color: var(--tinta);
  box-shadow: 0 6px 18px rgba(70, 45, 30, 0.06), inset 0 1px 0 rgba(255, 255, 255, 0.9);
}
.marca svg { width: 18px; height: 18px; }

.brand {
  font-size: 17px;
  font-weight: 600;
  letter-spacing: -0.025em;
  color: var(--tinta);
  line-height: 1.2;
  white-space: nowrap;
}
.brand small {
  display: block;
  font-family: 'Inter', sans-serif !important;
  font-size: 8.5px;
  font-weight: 500;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: var(--tenue);
  margin-top: 4px;
}

[data-testid="stVerticalBlock"].st-key-chat,
[data-testid="stVerticalBlock"].st-key-consola {
  position: relative;
  background: var(--cristal) !important;
  -webkit-backdrop-filter: blur(30px) saturate(112%);
  backdrop-filter: blur(30px) saturate(112%);
  border: 1px solid var(--borde) !important;
  border-radius: var(--radio) !important;
  box-shadow:
    0 38px 90px -52px rgba(70, 45, 30, 0.45),
    0 8px 26px rgba(70, 45, 30, 0.05),
    inset 0 1px 0 rgba(255, 255, 255, 0.9);
  overflow: hidden;
}

.st-key-chat::before,
.st-key-consola::before {
  content: "";
  position: absolute;
  inset: 0;
  border-radius: inherit;
  padding: 1px;
  background: linear-gradient(135deg,
    rgba(255, 255, 255, 0.95) 0%,
    rgba(232, 103, 90, 0.16) 38%,
    rgba(105, 172, 160, 0.10) 66%,
    rgba(255, 255, 255, 0.75) 100%);
  -webkit-mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
  -webkit-mask-composite: xor;
  mask: linear-gradient(#000 0 0) content-box, linear-gradient(#000 0 0);
  mask-composite: exclude;
  pointer-events: none;
  z-index: 4;
}

[data-testid="stVerticalBlock"].st-key-chat {
  height: calc(100dvh - 126px);
  min-height: 470px;
  display: flex !important;
  flex-direction: column !important;
  gap: 0 !important;
  padding: 0 !important;
  flex: 0 0 auto !important;
}

[data-testid="stVerticalBlock"].st-key-consola {
  height: calc(100dvh - 126px);
  min-height: 470px;
  display: flex !important;
  flex-direction: column !important;
  gap: 0 !important;
  overflow: hidden;
  padding: 0 !important;
  flex: 0 0 auto !important;
}
.st-key-consola > [data-testid="stLayoutWrapper"]:has(> .st-key-consola_head) {
  flex: 0 0 auto !important;
}
.st-key-consola > [data-testid="stLayoutWrapper"]:has(> .st-key-consola_scroll) {
  flex: 1 1 0 !important;
  min-height: 0 !important;
  overflow: hidden !important;
}
.st-key-consola_head {
  flex: 0 0 auto !important;
  gap: 0 !important;
  padding: 24px 24px 16px !important;
  border-bottom: 1px solid var(--borde-suave);
}
.st-key-consola_scroll {
  flex: 1 1 auto !important;
  min-height: 0 !important;
  overflow-y: auto;
  overflow-x: hidden;
  scrollbar-width: none;
  -ms-overflow-style: none;
  gap: 0 !important;
  padding: 12px 24px 20px !important;
}
.st-key-consola_scroll::-webkit-scrollbar { display: none; width: 0; height: 0; }

[data-testid="stVerticalBlock"].st-key-chat {
  container-type: inline-size;
}
.st-key-chat > [data-testid="stLayoutWrapper"]:has(> .st-key-chat_head) {
  flex: 0 0 auto !important;
}
.st-key-chat > [data-testid="stLayoutWrapper"]:has(> .st-key-chat_scroll) {
  flex: 1 1 0 !important;
  min-height: 0 !important;
  overflow: hidden !important;
}
.st-key-chat > [data-testid="stElementContainer"]:has([data-testid="stChatInput"]) {
  flex: 0 0 auto !important;
  min-height: 0 !important;
}

.st-key-chat_head {
  display: flex !important;
  flex-direction: row !important;
  align-items: center !important;
  justify-content: space-between !important;
  gap: 12px !important;
  padding: 20px 26px 0 !important;
  flex: 0 0 auto !important;
}
.st-key-chat_head .rotulo {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 9px;
  font-weight: 600;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: var(--tenue);
}
.st-key-chat_head .rotulo::before {
  content: "";
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--rojo);
}
.st-key-chat_head [data-testid="stElementContainer"] {
  flex: 0 0 auto !important;
  width: auto !important;
}
.st-key-chat_head [data-testid="stButton"] {
  width: auto !important;
  flex: 0 0 auto !important;
}
.st-key-chat_head button {
  width: max-content !important;
  min-width: max-content !important;
  min-height: 0 !important;
  padding: 6px 10px !important;
  white-space: nowrap !important;
  background: transparent !important;
  border: 1px solid transparent !important;
  border-radius: 999px !important;
  color: var(--tenue) !important;
  font-size: 9px !important;
  font-weight: 500 !important;
  letter-spacing: 0.12em !important;
  text-transform: uppercase !important;
  transition: color 0.2s ease, border-color 0.2s ease, background 0.2s ease;
}
.st-key-chat_head button p,
.st-key-descargar_grafica button p {
  font-size: 9px !important;
  font-weight: 500 !important;
  letter-spacing: 0.12em !important;
  line-height: 1.4 !important;
  text-transform: uppercase !important;
  color: inherit !important;
  margin: 0 !important;
}
.st-key-descargar_grafica button p {
  letter-spacing: 0.10em !important;
}
.st-key-chat_head button:hover {
  color: var(--rojo) !important;
  border-color: rgba(200, 16, 46, 0.16) !important;
  background: rgba(200, 16, 46, 0.04) !important;
}

.st-key-chat_scroll {
  flex: 1 1 auto !important;
  height: 100% !important;
  min-height: 0 !important;
  overflow-y: auto;
  overflow-x: hidden;
  padding: 16px 26px 10px !important;
  display: flex !important;
  flex-direction: column !important;
  gap: 0 !important;
  scrollbar-width: none;
  -ms-overflow-style: none;
}
.st-key-chat_scroll::-webkit-scrollbar { display: none; width: 0; height: 0; }
.st-key-chat_scroll:has(.hero) { justify-content: center; }

.hero {
  position: relative;
  display: flex;
  flex-direction: column;
  align-items: flex-start;
  text-align: left;
  gap: 13px;
  padding: 0 4%;
  max-width: 760px;
  margin: 0;
}
.hero::before {
  content: "";
  position: absolute;
  left: 12%;
  top: 46%;
  width: min(44vw, 460px);
  aspect-ratio: 1;
  transform: translate(-50%, -50%);
  border-radius: 50%;
  background: radial-gradient(circle,
    rgba(232, 103, 90, 0.18) 0%,
    rgba(225, 175, 115, 0.09) 44%,
    rgba(232, 103, 90, 0) 72%);
  filter: blur(80px);
  -webkit-filter: blur(80px);
  pointer-events: none;
  z-index: 0;
}
.hero > * { position: relative; z-index: 1; }

.hero-kicker {
  display: flex;
  align-items: center;
  gap: 9px;
  font-size: 9px;
  font-weight: 600;
  letter-spacing: 0.17em;
  text-transform: uppercase;
  color: var(--rojo);
}
.hero-kicker::before {
  content: "";
  width: 26px;
  height: 1px;
  background: var(--rojo);
  opacity: 0.6;
}

.hero .t {
  font-size: clamp(30px, 3vw, 46px);
  font-weight: 300;
  letter-spacing: -0.045em;
  line-height: 1.05;
  color: var(--tinta);
  max-width: 640px;
}
.hero .s {
  font-size: 14px;
  line-height: 1.7;
  color: var(--cuerpo);
  max-width: 470px;
}

.st-key-chat_scroll [data-testid="stHorizontalBlock"] {
  margin-top: 30px;
  max-width: 1000px;
  width: 100%;
  margin-left: 0;
  margin-right: auto;
  gap: 12px !important;
}
.st-key-chat_scroll [data-testid="stHorizontalBlock"] button {
  position: relative;
  min-height: 124px;
  padding: 44px 18px 16px !important;
  align-items: flex-start !important;
  justify-content: flex-start !important;
  text-align: left !important;
  background-color: rgba(255, 255, 255, 0.40) !important;
  background-image: none !important;
  border: 1px solid var(--borde) !important;
  border-radius: 18px !important;
  box-shadow: 0 14px 34px -26px rgba(70, 45, 30, 0.30), inset 0 1px 0 rgba(255, 255, 255, 0.8);
  transition: background-color 0.25s ease, border-color 0.25s ease, transform 0.25s ease, box-shadow 0.25s ease;
}
.st-key-chat_scroll [data-testid="stHorizontalBlock"] button:hover {
  background-color: rgba(255, 255, 255, 0.70) !important;
  border-color: rgba(200, 16, 46, 0.24) !important;
  transform: translateY(-2px);
  box-shadow: 0 22px 44px -28px rgba(70, 45, 30, 0.34), inset 0 1px 0 rgba(255, 255, 255, 0.9);
}
.st-key-chat_scroll [data-testid="stHorizontalBlock"] button p {
  white-space: normal !important;
  text-align: left !important;
  font-size: 13px !important;
  line-height: 1.55 !important;
  font-weight: 400 !important;
  color: var(--tinta-2) !important;
  margin: 0 !important;
}
.st-key-chat_scroll [data-testid="stHorizontalBlock"] button::before {
  position: absolute;
  top: 15px;
  left: 18px;
  font-size: 8.5px;
  font-weight: 600;
  letter-spacing: 0.17em;
  color: var(--rojo);
}
.st-key-chat_scroll [data-testid="stHorizontalBlock"] button::after {
  content: "";
  position: absolute;
  top: 14px;
  right: 16px;
  width: 16px;
  height: 16px;
  background-position: center;
  background-repeat: no-repeat;
  background-size: 16px 16px;
  opacity: 0.55;
}
.st-key-sug_0 button::before { content: "ANÁLISIS"; }
.st-key-sug_1 button::before { content: "CALIDAD"; }
.st-key-sug_2 button::before { content: "NORMATIVA"; }
.st-key-sug_0 button::after { background-image: var(--ico-chart); }
.st-key-sug_1 button::after { background-image: var(--ico-db); }
.st-key-sug_2 button::after { background-image: var(--ico-book); }

[data-testid="stChatMessage"] {
  background: transparent !important;
  border: none !important;
  padding: 0 !important;
  box-shadow: none !important;
  width: fit-content;
  max-width: min(94%, 740px);
  gap: 10px;
  align-items: flex-start;
  margin-bottom: 12px;
}
[data-testid="stChatMessageContent"] {
  background: rgba(255, 255, 255, 0.62);
  -webkit-backdrop-filter: blur(22px) saturate(108%);
  backdrop-filter: blur(22px) saturate(108%);
  border: 1px solid var(--borde);
  border-radius: 18px 18px 18px 6px;
  padding: 13px 17px !important;
  box-shadow: 0 18px 40px -30px rgba(70, 45, 30, 0.34), inset 0 1px 0 rgba(255, 255, 255, 0.85);
}
[data-testid="stChatMessageContent"] [data-testid="stMarkdownContainer"],
[data-testid="stChatMessageContent"] [data-testid="stMarkdownContainer"] > * {
  margin-top: 0 !important;
  margin-bottom: 0 !important;
}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {
  margin-left: auto;
  flex-direction: row-reverse;
}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"])
  [data-testid="stChatMessageContent"] {
  background: var(--tinta);
  border-color: var(--tinta);
  border-radius: 18px 18px 6px 18px;
  box-shadow: 0 18px 40px -30px rgba(23, 25, 35, 0.55);
}
[data-testid="stChatMessageContent"] p {
  color: var(--tinta-2);
  font-size: 13.5px;
  line-height: 1.7;
  margin: 0;
  font-variant-numeric: tabular-nums;
}
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"])
  [data-testid="stChatMessageContent"] p,
[data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"])
  [data-testid="stChatMessageContent"] strong {
  color: #F8F4EC;
}
[data-testid="stChatMessageContent"] strong { color: var(--tinta); font-weight: 600; }

[data-testid="stChatMessageAvatarAssistant"] {
  width: 30px !important;
  height: 30px !important;
  min-width: 30px !important;
  border-radius: 50% !important;
  background-color: rgba(255, 255, 255, 0.70);
  background-image: var(--ico-marca);
  background-repeat: no-repeat;
  background-position: center;
  background-size: 17px 17px;
  border: 1px solid var(--borde) !important;
  box-shadow: 0 4px 14px rgba(70, 45, 30, 0.08);
  overflow: hidden;
}
[data-testid="stChatMessageAvatarAssistant"] > *,
[data-testid="stChatMessageAvatarUser"] > * { display: none !important; }

[data-testid="stChatMessageAvatarUser"] {
  width: 30px !important;
  height: 30px !important;
  min-width: 30px !important;
  border-radius: 50% !important;
  background-color: var(--tinta);
  background-image: var(--ico-user);
  background-repeat: no-repeat;
  background-position: center;
  background-size: 14px 14px;
  border: 1px solid var(--tinta);
  color: #F8F4EC !important;
}

.meta-mensaje {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  margin-top: 10px;
  padding-top: 8px;
  border-top: 1px solid var(--borde-suave);
  font-size: 8.5px;
  font-weight: 500;
  letter-spacing: 0.10em;
  text-transform: uppercase;
  color: var(--tenue);
}
.meta-agentes {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.meta-tiempo {
  flex: none;
  font-variant-numeric: tabular-nums;
  color: var(--plata);
}

.analizando {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 10px 2px 14px;
  color: var(--tenue);
}
.analizando-ico {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 30px;
  height: 30px;
  flex: none;
  border-radius: 50%;
  border: 1px solid var(--borde);
  background: rgba(255, 255, 255, 0.55);
  color: var(--rojo);
}
.analizando-ico svg { width: 14px; height: 14px; animation: girar 1.15s linear infinite; }
.analizando strong {
  display: block;
  font-size: 12px;
  font-weight: 500;
  color: var(--tinta-2);
}
.analizando small {
  display: block;
  margin-top: 2px;
  font-size: 10.5px;
  color: var(--tenue);
}
@keyframes girar { to { transform: rotate(360deg); } }

.st-key-chat [data-testid="stChatInput"] {
  flex: 0 0 auto !important;
  margin: 0 20px 18px !important;
  width: auto !important;
  background: rgba(255, 255, 255, 0.72) !important;
  -webkit-backdrop-filter: blur(22px) saturate(110%);
  backdrop-filter: blur(22px) saturate(110%);
  border: 1px solid var(--borde) !important;
  border-radius: 16px !important;
  box-shadow: 0 18px 44px -32px rgba(70, 45, 30, 0.42), inset 0 1px 0 rgba(255, 255, 255, 0.9);
  transition: border-color 0.25s ease, box-shadow 0.25s ease;
}
.st-key-chat [data-testid="stChatInput"]:focus-within {
  border-color: rgba(200, 16, 46, 0.32) !important;
  box-shadow:
    0 0 0 3px rgba(200, 16, 46, 0.06),
    0 18px 44px -32px rgba(70, 45, 30, 0.42),
    inset 0 1px 0 rgba(255, 255, 255, 0.95);
}
.st-key-chat [data-testid="stChatInput"] > div,
.st-key-chat [data-testid="stChatInput"] div,
.st-key-chat [data-testid="stChatInput"] textarea {
  border-color: transparent !important;
  background: transparent !important;
  box-shadow: none !important;
}
.st-key-chat [data-testid="stChatInput"] { color: var(--tinta) !important; }
.st-key-chat [data-testid="stChatInputTextArea"] {
  color: var(--tinta);
  font-size: 13px;
  font-variant-numeric: tabular-nums;
  line-height: 20px !important;
  height: 40px !important;
  min-height: 40px !important;
  padding: 10px 48px 10px 0 !important;
}
.st-key-chat [data-testid="stChatInputTextArea"]::placeholder {
  color: rgba(122, 116, 107, 0.78);
}
.st-key-chat [data-testid="stChatInputSubmitButton"] {
  width: 32px !important;
  height: 32px !important;
  background-color: var(--rojo) !important;
  background-image: var(--ico-enviar);
  background-repeat: no-repeat;
  background-position: center;
  background-size: 15px 15px;
  border-radius: 10px !important;
  transition: background-color 0.2s ease;
}
.st-key-chat [data-testid="stChatInputSubmitButton"] svg { display: none !important; }
.st-key-chat [data-testid="stChatInputSubmitButton"]:hover {
  background-color: #A60D26 !important;
}

.st-key-btn_cerrar,
.st-key-btn_abrir,
.st-key-btn_tema {
  display: flex;
  justify-content: flex-end;
  width: 100% !important;
}
.st-key-btn_cerrar button,
.st-key-btn_abrir button,
.st-key-btn_tema button {
  width: 36px !important;
  height: 36px !important;
  min-height: 36px !important;
  padding: 0 !important;
  border-radius: 50% !important;
  background-color: rgba(255, 255, 255, 0.55) !important;
  border: 1px solid var(--borde) !important;
  background-repeat: no-repeat !important;
  background-position: center !important;
  background-size: 16px 16px !important;
  box-shadow: 0 6px 18px rgba(70, 45, 30, 0.06);
  transition: background-color 0.2s ease, border-color 0.2s ease, transform 0.2s ease;
}
.st-key-btn_cerrar button { background-image: var(--ico-cerrar) !important; }
.st-key-btn_abrir button { background-image: var(--ico-abrir) !important; }
.st-key-btn_tema button { background-image: var(--ico-tema) !important; }
.st-key-btn_cerrar button:hover,
.st-key-btn_abrir button:hover,
.st-key-btn_tema button:hover {
  background-color: rgba(255, 255, 255, 0.85) !important;
  border-color: rgba(23, 25, 35, 0.20) !important;
  transform: translateY(-1px);
}
.st-key-btn_cerrar button p,
.st-key-btn_abrir button p,
.st-key-btn_tema button p {
  font-size: 0 !important;
  line-height: 0 !important;
  color: transparent !important;
  margin: 0 !important;
}
.st-key-btn_cerrar button div,
.st-key-btn_abrir button div,
.st-key-btn_tema button div { margin: 0 !important; padding: 0 !important; }

.consola-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
}
.rotulo {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 9px;
  font-weight: 600;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: var(--tenue);
}
.rotulo::before {
  content: "";
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--rojo);
}
.estado {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  flex: none;
  padding: 4px 8px;
  border: 1px solid var(--borde);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.40);
  font-size: 8px;
  font-weight: 600;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--tenue);
  white-space: nowrap;
}
.estado .dot {
  width: 5px;
  height: 5px;
  border-radius: 50%;
  background: var(--plata);
}
.estado.ok { color: #246B61; border-color: rgba(47, 127, 115, 0.18); }
.estado.ok .dot { background: var(--teal); box-shadow: 0 0 0 3px rgba(47, 127, 115, 0.10); }
.estado.pensando { color: #8A5A16; border-color: rgba(169, 111, 31, 0.20); }
.estado.pensando .dot { background: var(--ambar); animation: latir 1.4s ease-in-out infinite; }
.estado.error { color: #8A1B2E; border-color: rgba(200, 16, 46, 0.20); }
.estado.error .dot { background: var(--rojo); }
@keyframes latir {
  0%, 100% { opacity: 0.45; }
  50% { opacity: 1; }
}

.traza {
  display: flex;
  flex-direction: column;
  margin-top: 18px;
}
.paso {
  position: relative;
  display: grid;
  grid-template-columns: 34px minmax(0, 1fr) auto;
  gap: 12px;
  padding-bottom: 18px;
}
.paso::before {
  content: "";
  position: absolute;
  left: 16px;
  top: 34px;
  bottom: 0;
  width: 1px;
  background: linear-gradient(180deg, rgba(200, 16, 46, 0.28) 0%, rgba(23, 25, 35, 0.10) 100%);
}
.paso.ultimo::before { display: none; }
.nodo {
  position: relative;
  width: 34px;
  height: 34px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  background: rgba(255, 255, 255, 0.66);
  border: 1px solid var(--borde);
  color: var(--tinta-2);
  box-shadow: 0 4px 12px rgba(70, 45, 30, 0.05), inset 0 1px 0 rgba(255, 255, 255, 0.9);
}
.nodo svg { width: 15px; height: 15px; }
.nodo::after {
  content: "";
  position: absolute;
  right: -1px;
  bottom: -1px;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--teal);
  border: 2px solid var(--papel-2);
  box-shadow: 0 0 0 2px rgba(47, 127, 115, 0.10);
}
.paso[data-estado="alerta"] .nodo {
  border-color: rgba(169, 111, 31, 0.28);
  color: #8A5A16;
}
.paso[data-estado="alerta"] .nodo::after { background: var(--ambar); box-shadow: 0 0 0 2px rgba(169, 111, 31, 0.10); }
.paso[data-estado="error"] .nodo {
  border-color: rgba(200, 16, 46, 0.28);
  color: #8A1B2E;
}
.paso[data-estado="error"] .nodo::after { background: var(--rojo); box-shadow: 0 0 0 2px rgba(200, 16, 46, 0.10); }
.paso.ultimo .nodo {
  border-color: rgba(200, 16, 46, 0.22);
  box-shadow: 0 0 0 4px rgba(200, 16, 46, 0.04), inset 0 1px 0 rgba(255, 255, 255, 0.9);
}

.paso-cuerpo {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
  padding-top: 1px;
}
.paso-nombre {
  font-size: 12px;
  font-weight: 600;
  letter-spacing: -0.01em;
  color: var(--tinta);
  line-height: 1.3;
}
.paso-accion {
  font-size: 11px;
  line-height: 1.45;
  color: var(--tenue);
}
.paso-detalle {
  font-size: 10.5px;
  line-height: 1.45;
  color: var(--cuerpo);
  opacity: 0.86;
}
.paso-dur {
  font-size: 9px;
  font-variant-numeric: tabular-nums;
  color: var(--plata);
  padding-top: 4px;
  white-space: nowrap;
}

.seccion {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin: 20px 0 9px;
  padding-top: 14px;
  border-top: 1px solid var(--borde-suave);
}
.seccion-t {
  font-size: 8.5px;
  font-weight: 600;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: var(--tenue);
}
.cuenta {
  font-size: 9px;
  font-variant-numeric: tabular-nums;
  color: var(--plata);
}

.aviso {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 11px 13px;
  margin-bottom: 8px;
  border-radius: 12px;
  background: rgba(214, 158, 46, 0.09);
  border: 1px solid rgba(169, 111, 31, 0.20);
  color: #6B4A16;
}
.aviso-ico {
  flex: none;
  display: flex;
  margin-top: 1px;
  color: var(--ambar);
}
.aviso-ico svg { width: 14px; height: 14px; }
.aviso strong {
  display: block;
  font-size: 8.5px;
  font-weight: 600;
  letter-spacing: 0.13em;
  text-transform: uppercase;
  color: #8A5A16;
  margin-bottom: 3px;
}
.aviso span {
  display: block;
  font-size: 11px;
  line-height: 1.55;
  color: #6B4A16;
}
.aviso.error {
  background: rgba(200, 16, 46, 0.05);
  border-color: rgba(200, 16, 46, 0.18);
  color: #8A1B2E;
}
.aviso.error .aviso-ico,
.aviso.error strong { color: var(--rojo); }
.aviso.error span { color: #8A1B2E; }

[data-testid="stExpander"] {
  border: 1px solid var(--borde) !important;
  border-radius: 12px !important;
  background: rgba(255, 255, 255, 0.40) !important;
  overflow: hidden;
}
[data-testid="stExpander"] summary {
  padding: 9px 12px !important;
  font-size: 10.5px !important;
  color: var(--tinta-2) !important;
  background: transparent !important;
}
[data-testid="stExpander"] summary svg {
  color: var(--tenue) !important;
  fill: var(--tenue) !important;
}
[data-testid="stExpander"] summary p {
  font-size: 10.5px !important;
  font-weight: 500 !important;
  color: var(--tinta-2) !important;
  margin: 0 !important;
}
[data-testid="stExpander"] summary:hover { color: var(--rojo) !important; }
[data-testid="stExpander"] summary:hover p { color: var(--rojo) !important; }
[data-testid="stExpander"] [data-testid="stExpanderDetails"] {
  padding: 0 10px 10px !important;
}

[data-testid="stCode"] {
  border: 1px solid var(--borde) !important;
  border-radius: 11px !important;
  overflow: hidden;
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.8);
}
[data-testid="stCode"] pre {
  background: #FBF8F2 !important;
  font-variant-numeric: tabular-nums;
}
[data-testid="stCode"] pre,
[data-testid="stCode"] code,
[data-testid="stCode"] .token {
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace !important;
  font-size: 10.5px !important;
  line-height: 1.65 !important;
  color: #2A2622 !important;
}
[data-testid="stCode"] .token.keyword { color: #8A1B2E !important; }
[data-testid="stCode"] .token.function { color: #2F7F73 !important; }
[data-testid="stCode"] .token.string { color: #8A5A16 !important; }
[data-testid="stCode"] .token.number { color: #4C5B8A !important; }
[data-testid="stCode"] .token.operator { color: #C8102E !important; }
[data-testid="stCode"] .token.punctuation { color: #6A645C !important; }
[data-testid="stCode"] .token.comment { color: #7A746B !important; }

[data-testid="stDataFrame"] {
  border: 1px solid var(--borde);
  border-radius: 12px;
  overflow: hidden;
  font-variant-numeric: tabular-nums;
}

.tabla-datos-wrap {
  border: 1px solid var(--borde);
  border-radius: 12px;
  overflow-x: auto;
  background: rgba(255, 255, 255, 0.30);
  box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.75);
}
.tabla-datos {
  width: 100%;
  border-collapse: collapse;
  font-size: 11.5px;
  margin: 0 !important;
}
.tabla-datos thead th {
  padding: 8px 11px;
  background: rgba(239, 233, 223, 0.64);
  border-bottom: 1px solid var(--borde);
  font-family: 'Inter', sans-serif !important;
  font-size: 8px;
  font-weight: 600;
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--tenue);
  text-align: left;
  white-space: nowrap;
}
.tabla-datos tbody td {
  padding: 8px 11px;
  border-bottom: 1px solid var(--borde-suave);
  color: var(--tinta-2);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.tabla-datos tbody tr:last-child td { border-bottom: none; }
.tabla-datos tbody tr:nth-child(even) { background: rgba(23, 25, 35, 0.018); }
.tabla-datos tbody tr:hover { background: rgba(200, 16, 46, 0.035); }
.tabla-datos .num { text-align: right; }
.tabla-datos .txt { text-align: left; }

[data-testid="stChatMessageContent"] :not(pre) > code {
  padding: 1px 5px;
  border: 1px solid rgba(200, 16, 46, 0.12);
  border-radius: 5px;
  background: rgba(200, 16, 46, 0.055);
  color: #8A1B2E;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace !important;
  font-size: 10.5px;
}

[data-testid="stImage"] img {
  border-radius: 12px;
  border: 1px solid var(--borde);
  box-shadow: 0 20px 50px -32px rgba(70, 45, 30, 0.35);
}

.st-key-descargar_grafica {
  display: flex;
  justify-content: flex-end;
  margin-top: -4px;
}
.st-key-descargar_grafica button {
  width: auto !important;
  min-height: 0 !important;
  padding: 3px 0 !important;
  background: transparent !important;
  border: none !important;
  color: var(--tenue) !important;
  font-size: 9px !important;
  letter-spacing: 0.10em !important;
  text-transform: uppercase !important;
}
.st-key-descargar_grafica button:hover { color: var(--rojo) !important; }

.fuentes {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}
.fuente {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  max-width: 100%;
  padding: 5px 9px;
  border: 1px solid var(--borde);
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.38);
  font-size: 10px;
  color: var(--cuerpo);
}
.fuente svg { width: 12px; height: 12px; flex: none; color: var(--tenue); }
.fuente span {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.pie-consola {
  margin-top: 18px;
  padding-top: 12px;
  border-top: 1px solid var(--borde-suave);
  font-size: 8.5px;
  letter-spacing: 0.10em;
  text-transform: uppercase;
  color: var(--plata);
}

.st-key-consola_scroll:has(.vacio-consola) {
  justify-content: center;
  align-items: stretch;
}
.vacio-consola {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  width: 100%;
  text-align: center;
  gap: 10px;
  padding: 24px 4px;
}
.vacio-ico {
  display: flex;
  color: var(--tenue);
  opacity: 0.55;
  margin-bottom: 4px;
}
.vacio-ico svg { width: 38px; height: 38px; }
.vacio-ico.girando { color: var(--rojo); opacity: 0.85; }
.vacio-ico.girando svg { animation: girar 1.15s linear infinite; }
.vacio-consola .t {
  font-size: 17px;
  line-height: 1.3;
  font-weight: 600;
  color: var(--tinta-2);
}
.vacio-consola .s {
  font-size: 10.5px;
  line-height: 1.65;
  color: var(--tenue);
  max-width: 280px;
}

.st-key-chat .shimmer {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 2px;
  z-index: 6;
  border-radius: var(--radio) var(--radio) 0 0;
  background: linear-gradient(90deg,
    rgba(200, 16, 46, 0) 0%,
    rgba(200, 16, 46, 0.55) 34%,
    rgba(232, 103, 90, 0.85) 50%,
    rgba(200, 16, 46, 0.45) 66%,
    rgba(200, 16, 46, 0) 100%);
  background-size: 46% 100%;
  background-repeat: no-repeat;
  animation: barrido 1.5s ease-in-out infinite;
  pointer-events: none;
}
@keyframes barrido {
  0%   { background-position: -46% 0; }
  100% { background-position: 146% 0; }
}

::-webkit-scrollbar { width: 6px; height: 6px; }
::-webkit-scrollbar-thumb { background: rgba(23, 25, 35, 0.16); border-radius: 999px; }
::-webkit-scrollbar-thumb:hover { background: rgba(23, 25, 35, 0.28); }
::-webkit-scrollbar-track { background: transparent; }

@container (min-width: 1200px) {
  .st-key-chat .hero {
    margin-left: auto;
    margin-right: auto;
  }
  .st-key-chat .st-key-chat_scroll [data-testid="stHorizontalBlock"] {
    margin-left: auto;
    margin-right: auto;
  }
}

@media (max-width: 900px) {
  [data-testid="stMainBlockContainer"] { padding: 0.9rem 0.8rem 1.2rem; }

  [data-testid="stVerticalBlock"].st-key-chat {
    height: 70dvh;
    min-height: 420px;
  }
  [data-testid="stVerticalBlock"].st-key-consola {
    height: auto;
    min-height: 0;
    max-height: none;
    margin-top: 12px;
    padding: 0 !important;
  }
  .st-key-consola > [data-testid="stLayoutWrapper"]:has(> .st-key-consola_scroll) {
    flex: 0 0 auto !important;
    min-height: 0 !important;
    overflow: visible !important;
  }
  .st-key-consola_head { padding: 20px 18px 13px !important; }
  .st-key-consola_scroll {
    flex: 0 0 auto !important;
    overflow: visible !important;
    padding: 12px 18px 18px !important;
  }

  .st-key-chat_head { padding: 16px 18px 0 !important; }
  .st-key-chat_scroll { padding: 12px 18px 6px !important; }
  .st-key-chat [data-testid="stChatInput"] { margin: 0 14px 14px !important; }

  .hero { padding: 0 2%; }
  .hero .t { font-size: 30px; }
  .hero .s { font-size: 13px; }
    .st-key-chat_scroll [data-testid="stHorizontalBlock"] { margin-top: 20px; }
  .st-key-chat_scroll [data-testid="stHorizontalBlock"] button {
    min-height: 104px;
    padding: 42px 15px 14px !important;
  }
  .st-key-chat_scroll [data-testid="stHorizontalBlock"] button p { font-size: 12px !important; }

  .brand { font-size: 15.5px; }
  .brand small { font-size: 8px; letter-spacing: 0.12em; }
  .marca { width: 32px; height: 32px; }
  .marca svg { width: 16px; height: 16px; }
  .traza { margin-top: 15px; }
}

@media (max-width: 600px) {
  [data-testid="stVerticalBlock"].st-key-chat { height: 74dvh; }
  .st-key-chat_head .rotulo { font-size: 8px; }
  .meta-mensaje { font-size: 7.5px; letter-spacing: 0.08em; }
  .paso { grid-template-columns: 30px minmax(0, 1fr) auto; gap: 10px; }
  .nodo { width: 30px; height: 30px; }
  .nodo svg { width: 14px; height: 14px; }
  .paso::before { left: 15px; top: 30px; }
}

@media (prefers-reduced-motion: reduce) {
  .st-key-chat .shimmer,
  .analizando-ico svg,
  .vacio-ico.girando svg,
  .estado.pensando .dot { animation: none; }
}

[data-testid="stElementToolbarButtonContainer"] {
  background-color: rgba(255, 255, 255, 0.86) !important;
  border: 1px solid var(--borde) !important;
  box-shadow: 0 8px 20px -14px rgba(70, 45, 30, 0.35) !important;
}
[data-testid="stBaseButton-elementToolbar"] { color: var(--tinta) !important; }
[data-testid="stBaseButton-elementToolbar"] svg { fill: var(--tinta) !important; }
[data-testid="stCodeCopyButton"] { color: var(--tenue) !important; }
[data-testid="stCodeCopyButton"] svg { fill: var(--tenue) !important; }
</style>
"""
