from __future__ import annotations

import io
import json
import zipfile
import hashlib
import uuid
from datetime import datetime
from typing import Optional

import pandas as pd
import streamlit as st
import zlib
import base64
# QR (geração)
import qrcode
from PIL import Image

# Webcam QR (leitura)
import cv2
import av
from streamlit_webrtc import webrtc_streamer, VideoProcessorBase
from streamlit_autorefresh import st_autorefresh
import time

# =========================================================
# DADOS MOCK (40 produtos + cliente completo)
# =========================================================
def mock_produtos(n: int = 40) -> pd.DataFrame:
    rows = []
    for i in range(1, n + 1):
        codigo = f"P{i:05d}"
        descricao = f"Produto Demonstração {i:02d} - Linha Feira"
        estoque = 10 + (i * 3) % 80
        preco = round(19.9 + i * 2.35, 2)
        rows.append(
            {
                "codigo": codigo,
                "descricao": descricao,
                "estoque": int(estoque),
                "preco": float(preco),
            }
        )
    return pd.DataFrame(rows)


def mock_cliente_completo() -> dict:
    return {
        "cnpj": "12.345.678/0001-90",
        "razao_social": "MOVEIS & DECOR LTDA",
        "nome_fantasia": "Móveis & Decor",
        "inscricao_estadual": "123.456.789.112",
        "email": "compras@moveisedecor.com.br",
        "telefone": "(41) 99999-9999",
        "endereco": {
            "logradouro": "Av. Exemplo",
            "numero": "1234",
            "bairro": "Centro",
            "cidade": "Curitiba",
            "uf": "PR",
            "cep": "80000-000",
            "pais": "Brasil",
        },
        "contato": {
            "nome": "Maria Compras",
            "cargo": "Compras",
            "whatsapp": "(41) 98888-7777",
        },
        "condicao_pagamento": "28/35/42",
        "observacoes": "Cliente demonstrativo para validação do fluxo QR+ZIP.",
    }


# =========================================================
# UTIL: hash / QR / ZIP
# =========================================================
def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_qr_png(payload_text: str) -> bytes:
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=10,
        border=3,
    )
    qr.add_data(payload_text)
    qr.make(fit=True)
    img: Image.Image = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def build_order_inline_payload(cliente: dict, itens_pedido: pd.DataFrame, order_id: str) -> dict:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    itens = []
    for _, r in itens_pedido.iterrows():
        itens.append(
            {
                "c": str(r["codigo"]),
                "d": str(r["descricao"])[:40],  # corta p/ caber
                "q": int(r["qtde"]),
                "p": float(r["preco_unit"]),
            }
        )

    total = float(sum(i["q"] * i["p"] for i in itens))

    payload = {
        "type": "ORDER_INLINE",
        "v": 1,
        "order_id": order_id,
        "created_at": now,
        "cliente": {
            "cnpj": cliente.get("cnpj"),
            "razao": cliente.get("razao_social"),
            "fant": cliente.get("nome_fantasia"),
            "tel": cliente.get("telefone"),
            "cid": cliente.get("endereco", {}).get("cidade"),
            "uf": cliente.get("endereco", {}).get("uf"),
        },
        "itens": itens,
        "total": total,
    }
    return payload


def encode_qr_payload(payload: dict) -> str:
    """
    Compacta JSON -> zlib -> base64.
    Retorna um texto curto, bom para QR.
    """
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    comp = zlib.compress(raw, level=9)
    b64 = base64.urlsafe_b64encode(comp).decode("ascii")
    return "QR1:" + b64


def decode_qr_payload(text: str) -> dict:
    if not text.startswith("QR1:"):
        # fallback: se vier JSON puro
        return json.loads(text)

    b64 = text[4:]
    comp = base64.urlsafe_b64decode(b64.encode("ascii"))
    raw = zlib.decompress(comp)
    return json.loads(raw.decode("utf-8"))

def beep():
    # WebAudio: tende a funcionar melhor que <audio autoplay> no celular
    st.components.v1.html(
        """
        <script>
        (async () => {
          try {
            const AudioContext = window.AudioContext || window.webkitAudioContext;
            const ctx = new AudioContext();
            // garante que está "running"
            if (ctx.state === "suspended") { await ctx.resume(); }

            const o = ctx.createOscillator();
            const g = ctx.createGain();

            o.type = "sine";
            o.frequency.value = 880; // Hz (bipe agudo)
            g.gain.value = 0.08;     // volume

            o.connect(g);
            g.connect(ctx.destination);

            o.start();
            setTimeout(() => {
              o.stop();
              ctx.close();
            }, 120); // 120ms
          } catch (e) {
            console.log("beep blocked", e);
          }
        })();
        </script>
        """,
        height=0,
    )

def read_zip_bytes(zip_bytes: bytes) -> dict[str, bytes]:
    out: dict[str, bytes] = {}
    with zipfile.ZipFile(io.BytesIO(zip_bytes), "r") as z:
        for name in z.namelist():
            out[name] = z.read(name)
    return out


# =========================================================
# WEBCAM: leitor de QR Code com OpenCV (Otimizado)
# =========================================================
class QRVideoProcessor(VideoProcessorBase):
    """
    Leitor QR robusto com debounce para evitar flickering no Streamlit.
    """
    def __init__(self):
        self.detector = cv2.QRCodeDetector()
        self.last_text: Optional[str] = None
        self.last_status: str = "Aguardando QR..."
        self.last_decode_time = 0
        self.debounce_seconds = 2.0  # Tempo mínimo entre leituras

    def _try_decode(self, img_bgr):
        # 1) tentativa direta
        data, points, _ = self.detector.detectAndDecode(img_bgr)
        if data:
            return data, points

        # 2) grayscale + upscale + leve contraste
        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
        gray = cv2.equalizeHist(gray)
        up = cv2.resize(gray, None, fx=2.0, fy=2.0, interpolation=cv2.INTER_LINEAR)

        data2, points2, _ = self.detector.detectAndDecode(up)
        if data2:
            return data2, points2

        # 3) se detectou pontos mas não decodificou, tenta recortar ROI
        if points is not None:
            pts = points.astype(int).reshape(-1, 2)
            x, y, w, h = cv2.boundingRect(pts)
            pad = 20
            x0 = max(x - pad, 0)
            y0 = max(y - pad, 0)
            x1 = min(x + w + pad, img_bgr.shape[1])
            y1 = min(y + h + pad, img_bgr.shape[0])
            roi = img_bgr[y0:y1, x0:x1]

            data3, points3, _ = self.detector.detectAndDecode(roi)
            if data3:
                return data3, points3

        return "", points

    def recv(self, frame: av.VideoFrame) -> av.VideoFrame:
        img = frame.to_ndarray(format="bgr24")
        current_time = datetime.now().timestamp()

        data, points = self._try_decode(img)

        # Debounce: só atualiza se passou o tempo mínimo
        if data and (current_time - self.last_decode_time > self.debounce_seconds):
            self.last_text = data
            self.last_status = f"✅ QR lido ({len(data)} chars)"
            self.last_decode_time = current_time
            cv2.putText(img, "QR LIDO", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 0), 2)
        elif points is not None:
            pts = points.astype(int).reshape(-1, 2)
            for i in range(len(pts)):
                p1 = tuple(pts[i])
                p2 = tuple(pts[(i + 1) % len(pts)])
                cv2.line(img, p1, p2, (0, 255, 0), 2)
            
            if self.last_text:
                self.last_status = "⚠️ QR detectado, mantendo leitura anterior"
                cv2.putText(img, "DETECTADO", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
            else:
                self.last_status = "⚠️ Detectou QR, mas não decodificou (aproxime / melhore luz)"
                cv2.putText(img, "DETECTADO, NAO LIDO", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
        else:
            self.last_status = "Aguardando QR..."
            cv2.putText(img, "Aguardando QR...", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)

        return av.VideoFrame.from_ndarray(img, format="bgr24")


# =========================================================
# STREAMLIT APP
# =========================================================
st.set_page_config(page_title="Protótipo QR + ZIP (Android -> Desktop)", layout="wide")
st.title("📦 Protótipo de Validação — Fluxo QR Code + Arquivo ZIP")
st.caption("Simula Android exportando ZIP + QR e Desktop lendo QR (câmera) e importando ZIP com validação (SHA-256).")

# Estado
if "produtos" not in st.session_state:
    st.session_state["produtos"] = mock_produtos(40)
if "cliente" not in st.session_state:
    st.session_state["cliente"] = mock_cliente_completo()

tab_android, tab_desktop = st.tabs(["📱 Android (simulação)", "💻 Desktop (simulação)"])


# -------------------------
# ANDROID (simulação)
# -------------------------
with tab_android:
    st.subheader("📱 Android — montar pedido e gerar QR (pedido completo dentro do QR)")

    col1, col2 = st.columns([1.2, 0.8], gap="large")

    with col1:
        st.markdown("### Cliente (dados completos)")
        st.json(st.session_state["cliente"])

        st.markdown("### Catálogo (40 produtos)")
        produtos = st.session_state["produtos"].copy()
        st.dataframe(produtos, use_container_width=True, height=320)

    with col2:
        st.markdown("### Itens do pedido")
        options = (produtos["codigo"] + " — " + produtos["descricao"]).tolist()
        sel = st.multiselect("Selecione produtos", options=options)

        itens = []
        for opt in sel:
            codigo = opt.split(" — ")[0]
            p = produtos.loc[produtos["codigo"] == codigo].iloc[0]
            qtde = st.number_input(f"Qtde {codigo}", min_value=1, max_value=9999, value=1, step=1)
            itens.append(
                {
                    "codigo": codigo,
                    "descricao": p["descricao"],
                    "qtde": int(qtde),
                    "preco_unit": float(p["preco"]),
                    "subtotal": float(p["preco"]) * int(qtde),
                }
            )

        itens_df = pd.DataFrame(itens) if itens else pd.DataFrame(
            columns=["codigo", "descricao", "qtde", "preco_unit", "subtotal"]
        )

        st.dataframe(itens_df, use_container_width=True, height=220)

        total = float(itens_df["subtotal"].sum()) if not itens_df.empty else 0.0
        st.metric(
            "Total do Pedido",
            f"R$ {total:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."),
        )

        st.divider()

        if st.button("✅ Gerar QR (pedido completo)", use_container_width=True, disabled=itens_df.empty):
            order_id = uuid.uuid4().hex[:10].upper()

            qr_payload = build_order_inline_payload(
                cliente=st.session_state["cliente"],
                itens_pedido=itens_df.drop(columns=["subtotal"], errors="ignore"),
                order_id=order_id,
            )

            qr_text = encode_qr_payload(qr_payload)

            st.session_state["last_qr_text"] = qr_text
            st.session_state["last_qr_png"] = make_qr_png(qr_text)

        # ✅ MOSTRAR QR (não depende de ZIP)
        if "last_qr_png" in st.session_state:
            st.success("QR gerado! No Desktop, leia o QR para reproduzir o pedido completo.")
            st.image(st.session_state["last_qr_png"], use_container_width=True)

            with st.expander("Ver conteúdo do QR (compactado)"):
                st.code(st.session_state["last_qr_text"])


# -------------------------
# DESKTOP (simulação)
# -------------------------
with tab_desktop:
    st.subheader("💻 Desktop — ler QR e reproduzir o pedido completo (sem ZIP)")

    # --- estado base ---
    if "scanning" not in st.session_state:
        st.session_state["scanning"] = True
    if "scan_nonce" not in st.session_state:
        st.session_state["scan_nonce"] = 0
    if "audio_enabled" not in st.session_state:
        st.session_state["audio_enabled"] = False

    # ✅ colunas SEMPRE criadas (corrige seu erro)
    colA, colB = st.columns([1, 1], gap="large")

    with colA:
        st.markdown("### Leitor de QR Code")
        st.caption("Quando ler, toca bip, fecha a câmera e destaca o pedido.")

        # --- botões ---
        cbtn1, cbtn2, cbtn3 = st.columns(3)

        with cbtn1:
            if st.button("🔊 Ativar som", use_container_width=True):
                # Interação do usuário -> libera WebAudio no celular
                st.session_state["audio_enabled"] = True
                beep()  # teste imediato
                st.success("Som ativado ✅")

        with cbtn2:
            if st.button("🆕 Ler novo QR", use_container_width=True):
                st.session_state["scanning"] = True
                st.session_state["scan_nonce"] += 1
                for k in ["qr_text_from_cam","qr_captured_at","import_ok","import_pedido","qr_beeped","qr_text_area"]:
                    st.session_state.pop(k, None)
                st.rerun()

        with cbtn3:
            if st.button("🧹 Limpar", use_container_width=True):
                st.session_state["scanning"] = True
                for k in ["qr_text_from_cam","qr_captured_at","import_ok","import_pedido","qr_beeped","qr_text_area"]:
                    st.session_state.pop(k, None)
                st.rerun()

        # ✅ se já tem pedido, "fecha" a câmera e mostra banner grande
        if st.session_state.get("import_ok") and st.session_state.get("import_pedido"):
            st.session_state["scanning"] = False

            st.markdown(
                """
                <div style="
                    padding:16px;border-radius:14px;background:#0f172a;color:white;
                    font-size:26px;font-weight:800;text-align:center;margin-top:10px;">
                    ✅ PEDIDO CODIFICADO
                </div>
                """,
                unsafe_allow_html=True,
            )
            st.caption("Clique em **Ler novo QR** para escanear outro.")
            st.text_area(
                "Conteúdo do QR (texto)",
                value=st.session_state.get("qr_text_from_cam", ""),
                height=90,
                key="qr_text_area",
            )

        else:
            # --- seleção de câmera ---
            st.markdown("### Câmera")
            camera_mode_label = st.selectbox(
                "Escolha a câmera",
                ["Traseira (recomendada)", "Frontal"],
                index=0,
                key="camera_select",
            )
            facing_mode = "environment" if camera_mode_label.startswith("Traseira") else "user"

            if "camera_facing_mode" not in st.session_state:
                st.session_state["camera_facing_mode"] = facing_mode

            if st.session_state["camera_facing_mode"] != facing_mode:
                st.session_state["camera_facing_mode"] = facing_mode
                st.session_state["scan_nonce"] += 1
                for k in ["qr_text_from_cam","qr_captured_at","qr_beeped","qr_text_area"]:
                    st.session_state.pop(k, None)
                st.rerun()

            # visor menor
            st.markdown(
                """
                <style>
                video { max-width: 320px !important; max-height: 200px !important; border-radius: 10px; }
                </style>
                """,
                unsafe_allow_html=True,
            )

            # --- câmera só quando scanning=True ---
            if st.session_state.get("scanning", True):
                ctx = webrtc_streamer(
                    key=f"qr-reader-{st.session_state['camera_facing_mode']}-{st.session_state['scan_nonce']}",
                    video_processor_factory=QRVideoProcessor,
                    media_stream_constraints={
                        "video": {"facingMode": st.session_state["camera_facing_mode"]},
                        "audio": False,
                    },
                )

                if ctx.video_processor:
                    st.info(ctx.video_processor.last_status)

                if ctx.video_processor and not st.session_state.get("qr_text_from_cam"):
                    st_autorefresh(interval=600, key=f"qr_refresh_{st.session_state['scan_nonce']}")

                if ctx.video_processor and ctx.video_processor.last_text and not st.session_state.get("qr_text_from_cam"):
                    st.session_state["qr_text_from_cam"] = ctx.video_processor.last_text
                    st.session_state["qr_captured_at"] = datetime.now().strftime("%H:%M:%S")
                    st.rerun()

            qr_text = st.text_area(
                "Conteúdo do QR (texto)",
                value=st.session_state.get("qr_text_from_cam", ""),
                height=100,
                key="qr_text_area",
            )

            if st.session_state.get("qr_captured_at"):
                st.caption(f"Capturado às {st.session_state['qr_captured_at']}")

            # decodifica
            meta = None
            meta_err = None
            if qr_text.strip():
                try:
                    meta = decode_qr_payload(qr_text)
                except Exception as e:
                    meta_err = str(e)

            if meta and isinstance(meta, dict) and meta.get("type") == "ORDER_INLINE":
                # ✅ beep 1x (apenas se som foi ativado)
                if st.session_state.get("audio_enabled") and not st.session_state.get("qr_beeped"):
                    beep()
                    st.session_state["qr_beeped"] = True

                st.session_state["import_ok"] = True
                st.session_state["import_pedido"] = meta
                st.session_state["scanning"] = False  # fecha câmera
                st.rerun()
            elif qr_text.strip():
                st.error(f"QR lido, mas não consegui decodificar o pedido: {meta_err}")

    with colB:
        st.markdown("### Pedido reproduzido")
        if st.session_state.get("import_ok") and st.session_state.get("import_pedido"):
            pedido = st.session_state["import_pedido"]

            st.markdown("**Cliente**")
            st.json(pedido["cliente"])

            st.markdown("**Itens**")
            itens_df = pd.DataFrame(pedido["itens"]).rename(
                columns={"c": "codigo", "d": "descricao", "q": "qtde", "p": "preco_unit"}
            )
            itens_df["subtotal"] = itens_df["qtde"] * itens_df["preco_unit"]
            st.dataframe(itens_df, use_container_width=True, height=300)

            st.metric(
                "Total",
                f'R$ {float(pedido["total"]):,.2f}'.replace(",", "X").replace(".", ",").replace("X", "."),
            )
        else:
            st.info("Aguardando leitura do QR para reproduzir o pedido.")
