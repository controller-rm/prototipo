import io
import json
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


# =========================================================
# PRODUTOS FIXOS (TAB_PRODUTO)
# =========================================================
PRODUTOS_RAW = """
0000001 ; BULE COM INFUSOR - HORTICOOL GREEN 500ML ; 308,9
0000002 ; BULE COM INFUSOR - HORTICOOL GREEN 1000M ; 401,58
0000003 ; ACUCAREIRO HORTICOOL GREEN 150ML ; 142,94
0000004 ; LEITEIRA HORTICOOL GREEN 150ML ; 118,09
0000005 ; BULE COM INFUSOR E XICARA HOORTICOL GREE ; 358,33
0000006 ; XICARA DE CHA E PIRES APPLE BLOSSOM HORT ; 149,16
0000007 ; XICARA DE CHA E PIRES FLOR GEOMETRIA VER ; 149,16
0000008 ; CAFE E PIRES HORTICOOL GREEN 100ML ; 105,65
0000009 ; PRATO DE PAO LIMA HORTICOOL GREEN 16,5CM ; 93,21
0000010 ; PRATO DE PAO PINK HORTICOOL GREEN 16.5CM ; 93,21
0000011 ; PRATO DE SOBREMESA FLOR HORTICOOL GREEN ; 118,09
0000012 ; PRATO DE SOBREMESA GEOMETRIA GREEN HORTI ; 118,09
0000013 ; PRATO DE JANTAR FLOR DE MACA HORTICOOL G ; 149,16
0000014 ; PRATO DE JANTAR GEOMETRIA HORTICOOL GREE ; 149,16
0000015 ; PRATO FUNDO FLOR DE MACA HORTICOOL GREEN ; 130,5
0000016 ; PRATO FUNDO GEOMETRIA HORTICOOL GREEN 22 ; 130,5
0000017 ; TRAVESSA OBLONGA HORTICOOL GREEN 30CM 29 ; 149,16
0000018 ; TRAVESSA OBLONGA HORTICOOL GREEN 35CM 34 ; 217,53
0000019 ; BOWL HORTICOOL GREEN 17CM 700ML ; 124,28
0000020 ; BOWL FLOR DE MACA HORTICOOL GREEN 17CM 7 ; 124,28
0000021 ; SOPEIRA GREEN HORTICOOL 2.4L 2400ML ; 864,93
0000022 ; CONJUNTO/2 CANECAS HORTICOOL 300ML ; 155,36
0000023 ; PRATO DE DOCES 2 CAMADAS HORTICOOL GREEN ; 236,16
0000024 ; PRATO DE DOCE 3 CAMADAS HORTICOOL GREEN ; 403,96
0000025 ; TRAVESSA OVAL HORTICOOL GREEN 31CM 30.9x ; 186,46
0000026 ; TIGELA FLOR DE MACA HORTICOOL GREEN 22CM ; 279,67
0000027 ; TIGELA FLOR DE MACA HORTICOOL GREEN 12CM ; 80,8
0000028 ; CESTA DE PIQUENIQUE HORTICOOL GREEN 38x2 ; 441,07
0000029 ; TRILHO DE MESA HORTICOOL ; 286,99
0000030 ; BULE COM INFUSOR - HORTICOOL PINK 500ML ; 308,9
0000031 ; BULE COM INFUSOR - HORTICOOL PINK 1000ML ; 401,58
0000032 ; ACUCAREIRO HORTICOOL PINK 150ML ; 142,94
0000033 ; LEITEIRA HORTICOOL PINK 150ML ; 118,09
0000034 ; BULE COM INFUSOR E XICARA HORTICOOL PINK ; 358,33
0000035 ; XICARA DE CHA E PIRES APPLE BLOSSOM HORT ; 149,16
0000036 ; XICARA DE CHA E PIRES GEOMETRICA BLOSSOM ; 149,16
0000037 ; CAFE E PIRES HORTICOOL PINK 100ML ; 105,65
0000038 ; PRATO DE PAO LIME HORTICOOL PINK 16,5CM ; 93,21
0000039 ; PRATO DE SOBREMESA FLOR BLOSSOM PINK 20, ; 118,09
0000040 ; PRATO DE SOBREMESA GEOMETRIA HORTICOOL P ; 118,09
""".strip()


def parse_produtos(raw: str) -> pd.DataFrame:
    rows = []
    for line in raw.splitlines():
        parts = [p.strip() for p in line.split(";")]
        if len(parts) < 3:
            continue
        codigo = str(parts[0]).strip()
        descricao = str(parts[1]).strip()
        preco_txt = str(parts[2]).strip().replace(".", "").replace(",", ".")
        try:
            preco = float(preco_txt)
        except ValueError:
            preco = 0.0
        rows.append({"codigo": codigo.zfill(7), "descricao": descricao, "preco": preco})
    return pd.DataFrame(rows)


# =========================================================
# CLIENTE
# =========================================================
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
        "observacoes": "Cliente demonstrativo para validação do fluxo QR.",
    }


# =========================================================
# UTIL: QR
# =========================================================
def brl(v: float) -> str:
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def make_qr_png(payload_text: str) -> bytes:
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=16,
        border=6,
    )
    qr.add_data(payload_text)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def encode_qr_payload(payload: dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    comp = zlib.compress(raw, level=9)
    b64 = base64.urlsafe_b64encode(comp).decode("ascii")
    return "QR1:" + b64


def decode_qr_payload(text: str) -> dict:
    text = text.strip()

    if text.startswith("{"):
        return json.loads(text)

    if ":" in text:
        prefix, b64 = text.split(":", 1)
        if prefix in ("QR1", "ZIONNE_PEDIDO"):
            comp = base64.urlsafe_b64decode(b64.encode("ascii"))
            raw = zlib.decompress(comp)
            return json.loads(raw.decode("utf-8"))

    raise ValueError("Formato de QR não reconhecido")


import uuid
import streamlit as st

def beep():
    nonce = uuid.uuid4().hex  # força re-render do iframe
    st.components.v1.html(
        f"""
        <div id="{nonce}"></div>
        <script>
        (async () => {{
          try {{
            const AudioContext = window.AudioContext || window.webkitAudioContext;
            const ctx = new AudioContext();
            if (ctx.state === "suspended") {{ await ctx.resume(); }}

            const o = ctx.createOscillator();
            const g = ctx.createGain();
            o.type = "sine";
            o.frequency.value = 880;
            g.gain.value = 0.10;   // um pouco mais alto
            o.connect(g); g.connect(ctx.destination);
            o.start();
            setTimeout(() => {{ o.stop(); ctx.close(); }}, 140);
          }} catch (e) {{
            console.log(e);
          }}
        }})();
        </script>
        """,
        height=0,
    )

# =========================================================
# QR PAYLOAD (AGORA: cliente completo + itens mínimos)
# =========================================================
def build_order_payload_min(cliente: dict, carrinho: list[dict], order_id: str) -> dict:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    itens = []
    total = 0.0

    for item in carrinho:
        codigo = str(item["codigo"]).zfill(7)
        qtd = int(item["qtd"])
        preco = float(item["preco"])

        itens.append({
            "c": codigo,   # código produto
            "q": qtd,      # quantidade
            "p": preco     # preço unitário
        })

        total += qtd * preco

    # 🔹 cliente mínimo
    cliente_min = {
        "cnpj": cliente.get("cnpj"),
        "razao_social": cliente.get("razao_social")
    }

    return {
        "type": "ORDER_MIN",
        "v": 1,
        "order_id": order_id,
        "created_at": now,
        "cliente": cliente_min,  # ✅ agora só CNPJ + Razão Social
        "itens": itens,
        "total": float(total),
    }

# =========================================================
# WEBCAM: leitor QR
# =========================================================
import zxingcpp
import cv2
from datetime import datetime
from typing import Optional
from streamlit_webrtc import VideoProcessorBase

class QRVideoProcessor(VideoProcessorBase):
    def __init__(self):
        self.last_text: Optional[str] = None
        self.last_status: str = "Aguardando QR..."
        self.last_decode_time = 0.0
        self.debounce_seconds = 1.2

    def _try_decode(self, img_bgr):
        # ZXing trabalha muito bem com grayscale
        h, w = img_bgr.shape[:2]
        pad = 0.12  # corta bordas -> efeito zoom
        x0 = int(w * pad); x1 = int(w * (1 - pad))
        y0 = int(h * pad); y1 = int(h * (1 - pad))
        img_bgr = img_bgr[y0:y1, x0:x1]
        gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)

        # 1) Upscale leve
        gray = cv2.resize(gray, None, fx=1.6, fy=1.6, interpolation=cv2.INTER_CUBIC)

        # 2) Contraste local (ajuda MUITO em QR denso)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
        gray = clahe.apply(gray)

        # 3) Sharpen leve
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
        blur = cv2.GaussianBlur(gray, (0, 0), 1.0)
        gray = cv2.addWeighted(gray, 1.6, blur, -0.6, 0)

        results = zxingcpp.read_barcodes(gray)
        if results:
            # pega o primeiro QR encontrado
            return results[0].text
        return ""

    def recv(self, frame):
        img = frame.to_ndarray(format="bgr24")
        now_ts = datetime.now().timestamp()

        data = self._try_decode(img)

        if data and (now_ts - self.last_decode_time > self.debounce_seconds):
            self.last_text = data
            self.last_status = f"✅ QR lido ({len(data)} chars)"
            self.last_decode_time = now_ts
        else:
            self.last_status = "Aguardando QR..."

        return frame


# =========================================================
# APP
# =========================================================
st.set_page_config(page_title="Protótipo — Pedido via QR", layout="wide")
st.title("🧾 Protótipo — Pedido via QR (Android → Desktop)")
st.caption("Android monta pedido offline e gera QR. Desktop lê o QR e reconstrói o pedido usando tab_produto para descrição.")


# Estado
if "cliente" not in st.session_state:
    st.session_state["cliente"] = mock_cliente_completo()
if "tab_produto" not in st.session_state:
    st.session_state["tab_produto"] = parse_produtos(PRODUTOS_RAW)
if "carrinho" not in st.session_state:
    st.session_state["carrinho"] = []  # lista de dicts


tab_android, tab_desktop, tab_produto = st.tabs(
    ["📱 Android (simulação)", "💻 Desktop (simulação)", "📦 tab_produto"]
)

# =========================================================
# TAB_PRODUTO
# =========================================================
with tab_produto:
    st.subheader("📦 tab_produto — código / descrição / preço")
    st.dataframe(st.session_state["tab_produto"], use_container_width=True, height=520)


# =========================================================
# ANDROID
# =========================================================
with tab_android:
    st.subheader("📱 Android — catálogo em cards + carrinho")

    produtos = st.session_state["tab_produto"].copy()

    st.markdown(
        """
        <style>
        .card-inner{padding:8px;}
        .product-sku{font-size:12px;color:#64748b;font-weight:600;margin-bottom:2px;}
        .product-desc{font-size:14px;font-weight:700;line-height:1.2;margin-bottom:6px;}
        .product-price{font-size:16px;font-weight:800;margin-bottom:8px;}
        </style>
        """,
        unsafe_allow_html=True,
    )

    col_left, col_right = st.columns([1.15, 0.85], gap="large")

    with col_left:
        st.markdown("### Cliente (dados completos)")
        st.json(st.session_state["cliente"])

        st.markdown("### Produtos (cards)")
        # mostra cards em grade 2 colunas
        grid_cols = st.columns(2, gap="small")
        rc = 0
        for _, row in produtos.iterrows():
            codigo = str(row["codigo"]).zfill(7)
            preco = float(row["preco"])
            col = grid_cols[rc % 2]
            with col:
                ja_no_carrinho = any(item["codigo"] == codigo for item in st.session_state.carrinho)

                card = st.container(border=True)
                with card:
                    st.markdown('<div class="card-inner">', unsafe_allow_html=True)
                    st.markdown(f'<div class="product-sku">SKU {codigo}</div>', unsafe_allow_html=True)
                    st.markdown(f'<div class="product-desc">{row["descricao"]}</div>', unsafe_allow_html=True)
                    st.markdown(f'<div class="product-price">{brl(preco)}</div>', unsafe_allow_html=True)

                    qtd = st.number_input(
                        "Qtd",
                        value=1,
                        min_value=1,
                        step=1,
                        key=f"qtd_{codigo}_{rc}",
                    )

                    if st.button(
                        "Adicionar ➕",
                        key=f"add_{codigo}_{rc}",
                        type="primary",
                        use_container_width=True,
                    ):
                        if ja_no_carrinho:
                            idx = next(
                                i for i, item in enumerate(st.session_state.carrinho)
                                if item["codigo"] == codigo
                            )
                            st.session_state.carrinho[idx]["qtd"] += int(qtd)
                            st.session_state.carrinho[idx]["total"] = (
                                st.session_state.carrinho[idx]["qtd"] * preco
                            )
                        else:
                            st.session_state.carrinho.append(
                                {
                                    "codigo": codigo,
                                    "qtd": int(qtd),
                                    "preco": preco,
                                    "total": float(int(qtd) * preco),
                                }
                            )
                        st.rerun()

                    st.markdown("</div>", unsafe_allow_html=True)

            rc += 1

    with col_right:
        st.markdown("### 🛒 Carrinho")

        if st.session_state.carrinho:
            df_cart = pd.DataFrame(st.session_state.carrinho).copy()
            df_cart["codigo"] = df_cart["codigo"].astype(str).str.zfill(7)

            # enriquece o carrinho com descrição só pra exibição no Android
            df_cart = df_cart.merge(
                produtos[["codigo", "descricao"]],
                on="codigo",
                how="left",
            )
            df_cart = df_cart[["codigo", "descricao", "qtd", "preco", "total"]]
            df_cart["preco"] = df_cart["preco"].astype(float)
            df_cart["total"] = df_cart["total"].astype(float)

            st.dataframe(df_cart, use_container_width=True, height=350)

            total = float(df_cart["total"].sum())
            st.metric("Total do Pedido", brl(total))

            c1, c2 = st.columns(2)
            with c1:
                if st.button("🧹 Limpar carrinho", use_container_width=True):
                    st.session_state.carrinho = []
                    for k in list(st.session_state.keys()):
                        if str(k).startswith("qtd_") or str(k).startswith("add_"):
                            pass
                    st.rerun()

            with c2:
                if st.button("✅ Gerar QR do pedido", use_container_width=True, type="primary"):
                    order_id = uuid.uuid4().hex[:10].upper()
                    payload = build_order_payload_min(
                        cliente=st.session_state["cliente"],
                        carrinho=st.session_state.carrinho,
                        order_id=order_id,
                    )
                    qr_text = encode_qr_payload(payload)
                    st.session_state["last_qr_text"] = qr_text
                    st.session_state["last_qr_png"] = make_qr_png(qr_text)
                    st.rerun()

        else:
            st.info("Carrinho vazio. Adicione produtos nos cards ao lado.")

        if "last_qr_png" in st.session_state:
            st.divider()
            st.success("QR gerado! Leia no Desktop para reconstruir o pedido.")
            st.image(st.session_state["last_qr_png"], width=520)
            with st.expander("Ver conteúdo do QR (compactado)"):
                st.code(st.session_state["last_qr_text"])


# =========================================================
# DESKTOP
# =========================================================
with tab_desktop:
    st.subheader("💻 Desktop — ler QR e reproduzir pedido (descrição via tab_produto)")
    if "webrtc_playing" not in st.session_state:
        st.session_state["webrtc_playing"] = True
    # estados base
    if "scanning" not in st.session_state:
        st.session_state["scanning"] = True
    if "scan_nonce" not in st.session_state:
        st.session_state["scan_nonce"] = 0
    if "audio_enabled" not in st.session_state:
        st.session_state["audio_enabled"] = False

    produtos = st.session_state["tab_produto"].copy()

    colA, colB = st.columns([1, 1], gap="large")

    with colA:
        st.markdown("### Leitor de QR Code")

        b1, b2, b3, b4 = st.columns(4)

        with b1:
            if st.button("🔊 Ativar som", use_container_width=True):
                st.session_state["audio_enabled"] = True
                beep()  # só pra testar
                st.success("Som ativado ✅")

        with b2:
            if st.button("🆕 Ler novo QR", use_container_width=True):
                st.session_state["scanning"] = True
                st.session_state["webrtc_playing"] = True
                for k in ["qr_text_from_cam", "import_ok", "import_pedido", "qr_beeped", "qr_text_area"]:
                    st.session_state.pop(k, None)
                st.rerun()

        with b3:
            if st.button("🧹 Limpar", use_container_width=True):
                st.session_state["scanning"] = True
                for k in ["qr_text_from_cam", "import_ok", "import_pedido", "qr_beeped", "qr_text_area"]:
                    st.session_state.pop(k, None)
                st.rerun()

        with b4:
            if st.button("🔄 Resetar câmera", use_container_width=True):
                # ✅ aqui força renegociação limpa do WebRTC
                st.session_state["webrtc_key_nonce"] += 1
                st.session_state["webrtc_playing"] = True
                st.session_state["scanning"] = True
                for k in ["qr_text_from_cam", "qr_beeped", "qr_text_area"]:
                    st.session_state.pop(k, None)
                st.rerun()

        # se já tem pedido, fecha câmera e destaca
        if st.session_state.get("import_ok") and st.session_state.get("import_pedido"):
            st.session_state["webrtc_playing"] = False
            st.session_state["scanning"] = False
            st.markdown(
                """
                <div style="padding:16px;border-radius:14px;background:#0f172a;color:white;
                            font-size:26px;font-weight:800;text-align:center;margin-top:10px;">
                    ✅ PEDIDO CODIFICADO
                </div>
                """,
                unsafe_allow_html=True,
            )
            st.caption("Clique em **Ler novo QR** para escanear outro.")

        else:
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
                
            if st.session_state.get("camera_facing_mode") != facing_mode:
                st.session_state["camera_facing_mode"] = facing_mode
                st.session_state["webrtc_key_nonce"] += 1  # ✅ força reset
                st.session_state["webrtc_playing"] = True
                st.session_state["scanning"] = True
                for k in ["qr_text_from_cam", "qr_beeped", "qr_text_area"]:
                    st.session_state.pop(k, None)
                st.rerun()

            st.markdown("""
            <style>
            /* Pega o video do webrtc e dá um frame fixo */
            video {
            width: 100% !important;
            max-width: 720px !important;
            height: 420px !important;     /* altura fixa para enquadramento */
            object-fit: cover !important; /* preenche sem distorcer */
            border-radius: 12px;
            background: #000;
            }
            </style>
            """, unsafe_allow_html=True)

            if st.session_state.get("scanning", True):
                from streamlit_webrtc import RTCConfiguration

                RTC_CONFIGURATION = RTCConfiguration(
                    {"iceServers": [{"urls": ["stun:stun.l.google.com:19302"]}]}
                )

                webrtc_key = f"qr-reader-{st.session_state['webrtc_key_nonce']}"

                ctx = webrtc_streamer(
                    key=webrtc_key,
                    video_processor_factory=QRVideoProcessor,
                    media_stream_constraints={
                        "video": {
                            "facingMode": st.session_state["camera_facing_mode"],  # ✅ string simples é mais compatível
                            "width": {"ideal": 1280},
                            "height": {"ideal": 720},
                            "frameRate": {"ideal": 24, "max": 30},
                            "advanced": [
                                {"focusMode": "continuous"},
                                {"exposureMode": "continuous"},
                                {"whiteBalanceMode": "continuous"},
                            ],
                        },
                        "audio": False,
                    },
                    rtc_configuration=RTC_CONFIGURATION,
                    desired_playing_state=st.session_state["webrtc_playing"],
                    async_processing=True,  # ✅ ajuda estabilidade
                )

                if ctx.video_processor:
                    st.info(ctx.video_processor.last_status)

                if ctx.video_processor and not st.session_state.get("qr_text_from_cam"):
                    st_autorefresh(interval=600, key=f"qr_refresh_{st.session_state['scan_nonce']}")

                if ctx.video_processor and ctx.video_processor.last_text and not st.session_state.get("qr_text_from_cam"):
                    st.session_state["qr_text_from_cam"] = ctx.video_processor.last_text

                    # ✅ BIP IMEDIATO AO LER (precisa do áudio habilitado por clique)
                    if st.session_state.get("audio_enabled") and not st.session_state.get("qr_beeped"):
                        beep()
                        st.session_state["qr_beeped"] = True

                    st.rerun()

        qr_text = st.text_area(
            "Conteúdo do QR (texto)",
            value=st.session_state.get("qr_text_from_cam", ""),
            height=110,
            key="qr_text_area",
        )

        if qr_text.strip() and not (st.session_state.get("import_ok") and st.session_state.get("import_pedido")):
            try:
                meta = decode_qr_payload(qr_text)
            except Exception as e:
                st.error(f"Não consegui decodificar o QR: {e}")
                meta = None

            if meta and isinstance(meta, dict) and meta.get("type") == "ORDER_MIN":
                # bip 1x (som precisa estar ativado por clique)
                if st.session_state.get("audio_enabled") and not st.session_state.get("qr_beeped"):
                    beep()
                    st.session_state["qr_beeped"] = True

                st.session_state["import_ok"] = True
                st.session_state["import_pedido"] = meta
                st.session_state["scanning"] = False
                st.rerun()

    with colB:
        st.markdown("### Pedido reproduzido")

        if st.session_state.get("import_ok") and st.session_state.get("import_pedido"):
            pedido = st.session_state["import_pedido"]

            st.markdown("**Cliente (completo)**")
            st.json(pedido["cliente"])

            # itens do QR: só c/q/p
            itens_min = pd.DataFrame(pedido["itens"]).rename(
                columns={"c": "codigo", "q": "qtde", "p": "preco_unit"}
            )
            itens_min["codigo"] = itens_min["codigo"].astype(str).str.zfill(7)
            itens_min["qtde"] = itens_min["qtde"].astype(int)
            itens_min["preco_unit"] = itens_min["preco_unit"].astype(float)

            # ✅ recupera descrição no tab_produto
            itens = itens_min.merge(
                produtos[["codigo", "descricao"]],
                on="codigo",
                how="left",
            )
            itens["descricao"] = itens["descricao"].fillna("**PRODUTO NÃO ENCONTRADO NA TAB_PRODUTO**")
            itens["subtotal"] = itens["qtde"] * itens["preco_unit"]

            st.markdown("**Itens**")
            st.dataframe(itens[["codigo", "descricao", "qtde", "preco_unit", "subtotal"]], use_container_width=True, height=320)

            st.metric("Total", brl(float(itens["subtotal"].sum())))

            with st.expander("Pedido (JSON bruto do QR)"):
                st.json(pedido)
        else:
            st.info("Aguardando leitura do QR para reproduzir o pedido.")
