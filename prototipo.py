# =========================================================
# ZIONNE — Protótipo Pedido via QR (Android -> Desktop)
# Revisado para performance:
# ✅ Carrinho O(1): índice por código (sem any()/next() a cada clique)
# ✅ Renderização de catálogo otimizada: busca + paginação (evita 40+ cards com widgets sempre)
# ✅ Leitura QR mais estável: pipeline leve + multi-scale + debounce + constraints agressivas
# ✅ Código limpo: sem imports duplicados, sem classes duplicadas, sem variáveis soltas
#
# Requisitos (pip):
# streamlit pandas qrcode pillow opencv-python-headless streamlit-webrtc streamlit-autorefresh zxing-cpp
# (em alguns ambientes: zxingcpp)
# =========================================================

from __future__ import annotations

import base64
import io
import json
import uuid
import zlib
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st

# QR (geração)
import qrcode

# Webcam QR (leitura)
import cv2
import numpy as np
import zxingcpp
from streamlit_autorefresh import st_autorefresh
from streamlit_webrtc import RTCConfiguration, VideoProcessorBase, webrtc_streamer

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


@st.cache_data(show_spinner=False)
def parse_produtos(raw: str) -> pd.DataFrame:
    rows: List[dict] = []
    for line in raw.splitlines():
        parts = [p.strip() for p in line.split(";")]
        if len(parts) < 3:
            continue
        codigo = str(parts[0]).strip().zfill(7)
        descricao = str(parts[1]).strip()
        preco_txt = str(parts[2]).strip().replace(".", "").replace(",", ".")
        try:
            preco = float(preco_txt)
        except ValueError:
            preco = 0.0
        rows.append({"codigo": codigo, "descricao": descricao, "preco": preco})

    df = pd.DataFrame(rows)
    if not df.empty:
        df["codigo"] = df["codigo"].astype(str).str.zfill(7)
        df["preco"] = df["preco"].astype(float)
    return df


@st.cache_data(show_spinner=False)
def produtos_lookup(df: pd.DataFrame) -> Dict[str, Tuple[str, float]]:
    """
    Retorna dict: codigo -> (descricao, preco)
    """
    d: Dict[str, Tuple[str, float]] = {}
    for r in df.itertuples(index=False):
        # r: (codigo, descricao, preco)
        d[str(r.codigo).zfill(7)] = (str(r.descricao), float(r.preco))
    return d


# =========================================================
# CLIENTE (mock)
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
# UTIL: formatação
# =========================================================
def brl(v: float) -> str:
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


# =========================================================
# QR: payload mínimo + encode/decode + geração PNG
# =========================================================
def build_order_payload_min(cliente: dict, carrinho_list: List[dict], order_id: str) -> dict:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    itens: List[dict] = []
    total = 0.0

    for item in carrinho_list:
        codigo = str(item.get("codigo", "")).zfill(7)
        qtd = int(item.get("qtd", 0))
        preco = float(item.get("preco", 0.0))

        if not codigo.strip() or qtd <= 0:
            continue

        itens.append({"c": codigo, "q": qtd, "p": preco})
        total += qtd * preco

    cliente_min = {
        "cnpj": cliente.get("cnpj"),
        "razao_social": cliente.get("razao_social"),
    }

    return {
        "type": "ORDER_MIN",
        "v": 1,
        "order_id": order_id,
        "created_at": now,
        "cliente": cliente_min,
        "itens": itens,
        "total": float(total),
    }


def encode_qr_payload(payload: dict) -> str:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    comp = zlib.compress(raw, level=9)
    b64 = base64.urlsafe_b64encode(comp).decode("ascii")
    return "QR1:" + b64


def decode_qr_payload(text: str) -> dict:
    text = (text or "").strip()

    if text.startswith("{"):
        return json.loads(text)

    if ":" in text:
        prefix, b64 = text.split(":", 1)
        if prefix in ("QR1", "ZIONNE_PEDIDO"):
            comp = base64.urlsafe_b64decode(b64.encode("ascii"))
            raw = zlib.decompress(comp)
            return json.loads(raw.decode("utf-8"))

    raise ValueError("Formato de QR não reconhecido")


def make_qr_png(payload_text: str) -> bytes:
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=12,  # menor = QR menos denso na tela (ajuda leitura) mantendo qualidade
        border=5,
    )
    qr.add_data(payload_text)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# =========================================================
# CARRINHO: estrutura rápida (índice por código) + view em lista
# =========================================================
@dataclass
class CartItem:
    codigo: str
    qtd: int
    preco: float  # unitário

    @property
    def total(self) -> float:
        return float(self.qtd) * float(self.preco)


def cart_init():
    st.session_state.setdefault("cart_index", {})  # codigo -> CartItem (ou dict)
    st.session_state.setdefault("carrinho", [])  # view compatível (list[dict])


def cart_sync_view():
    """
    Mantém st.session_state["carrinho"] como list[dict] para compatibilidade,
    mas os updates acontecem em cart_index (O(1)).
    """
    idx: Dict[str, CartItem] = st.session_state["cart_index"]
    st.session_state["carrinho"] = [
        {"codigo": k, "qtd": v.qtd, "preco": float(v.preco), "total": float(v.total)}
        for k, v in idx.items()
    ]


def cart_add(codigo: str, qtd: int, preco: float):
    codigo = str(codigo).zfill(7)
    qtd = int(qtd)
    preco = float(preco)

    if qtd <= 0:
        return

    idx: Dict[str, CartItem] = st.session_state["cart_index"]
    if codigo in idx:
        item = idx[codigo]
        item.qtd += qtd
        item.preco = preco  # atualiza preço (se mudar)
    else:
        idx[codigo] = CartItem(codigo=codigo, qtd=qtd, preco=preco)

    cart_sync_view()


def cart_clear():
    st.session_state["cart_index"] = {}
    st.session_state["carrinho"] = []


# =========================================================
# WEBCAM: leitor QR com pipeline otimizado
# =========================================================
class QRVideoProcessor(VideoProcessorBase):
    """
    Estável e rápido:
    - tenta decodificar a cada N frames (reduz CPU)
    - recorta centro (reduz ruído) sem exagerar
    - multi-scale + 2 variantes (CLAHE/sharpen e threshold)
    """

    def __init__(self):
        self.last_text: Optional[str] = None
        self.last_status: str = "Aguardando QR..."
        self._last_decode_ts = 0.0
        self._debounce_s = 0.9
        self._frame_i = 0

        self.scan_token = 0
        self._seen_token = 0

        # pré-cria CLAHE (evita overhead por frame)
        self._clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))

    def reset_for_new_scan(self):
        self.last_text = None
        self.last_status = "Aguardando QR..."
        self._last_decode_ts = 0.0

    @staticmethod
    def _crop_center(img_bgr: np.ndarray, pad: float = 0.07) -> np.ndarray:
        h, w = img_bgr.shape[:2]
        x0 = int(w * pad)
        x1 = int(w * (1 - pad))
        y0 = int(h * pad)
        y1 = int(h * (1 - pad))
        return img_bgr[y0:y1, x0:x1]

    def _prep_variants(self, gray: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        # V1: CLAHE + sharpen
        g1 = self._clahe.apply(gray)
        blur = cv2.GaussianBlur(g1, (0, 0), 1.0)
        g1s = cv2.addWeighted(g1, 1.6, blur, -0.6, 0)

        # V2: threshold adaptativo (ajuda quando foco/contraste está ruim)
        g2 = cv2.GaussianBlur(gray, (5, 5), 0)
        th = cv2.adaptiveThreshold(
            g2, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 2
        )

        return g1s, th

    def _try_decode(self, img_bgr: np.ndarray) -> str:
        # decodifica a cada 2 frames (~15fps se cam ~30fps)
        self._frame_i += 1
        if self._frame_i % 2 != 0:
            return ""

        img_bgr = self._crop_center(img_bgr, pad=0.07)
        gray0 = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)

        # duas escalas (ajuda QR pequeno / distância)
        for scale in (1.4, 2.0):
            gray = cv2.resize(gray0, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
            v1, v2 = self._prep_variants(gray)

            # tenta 2 variantes
            res = zxingcpp.read_barcodes(v1)
            if res:
                return res[0].text

            res = zxingcpp.read_barcodes(v2)
            if res:
                return res[0].text

        return ""

    def recv(self, frame):
        img = frame.to_ndarray(format="bgr24")
        now_ts = datetime.now().timestamp()

        # rearmar scan sem reconectar
        if self.scan_token != self._seen_token:
            self._seen_token = self.scan_token
            self.reset_for_new_scan()

        data = self._try_decode(img)

        if data and (now_ts - self._last_decode_ts > self._debounce_s):
            self.last_text = data
            self.last_status = "✅ QR detectado"
            self._last_decode_ts = now_ts
        else:
            self.last_status = "✅ QR detectado" if self.last_text else "Aguardando QR..."

        return frame


# =========================================================
# APP
# =========================================================
st.set_page_config(page_title="Protótipo — Pedido via QR", layout="wide")
st.title("🧾 Protótipo — Pedido via QR (Android → Desktop)")
st.caption(
    "Android monta pedido e gera QR. Desktop lê o QR e reconstrói itens com base na tab_produto."
)

# Estado base
st.session_state.setdefault("cliente", mock_cliente_completo())
st.session_state.setdefault("tab_produto", parse_produtos(PRODUTOS_RAW))
st.session_state.setdefault("last_qr_text", None)
st.session_state.setdefault("last_qr_png", None)
cart_init()

produtos_df = st.session_state["tab_produto"]
produtos_map = produtos_lookup(produtos_df)

tab_android, tab_desktop, tab_produto = st.tabs(
    ["📱 Android (simulação)", "💻 Desktop (simulação)", "📦 tab_produto"]
)

# =========================================================
# TAB_PRODUTO
# =========================================================
with tab_produto:
    st.subheader("📦 tab_produto — código / descrição / preço")
    st.dataframe(produtos_df, use_container_width=True, height=520)

# =========================================================
# ANDROID
# =========================================================
with tab_android:
    st.subheader("📱 Android — catálogo + carrinho (otimizado)")

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

        st.markdown("### Produtos (busca + paginação)")

        # 🔥 performance: filtra e mostra apenas um "slice" de produtos
        f1, f2, f3 = st.columns([0.62, 0.20, 0.18])
        with f1:
            q = st.text_input(
                "Buscar por código/descrição",
                placeholder="Ex: 0000012 ou GEOMETRIA",
                key="prod_search",
            )
        with f2:
            page_size = st.selectbox("Por página", [8, 12, 16, 20], index=1, key="page_size")
        with f3:
            # contador de página (1-based)
            st.session_state.setdefault("page", 1)

        df_f = produtos_df
        if q:
            qq = q.strip().lower()
            df_f = df_f[
                df_f["codigo"].astype(str).str.lower().str.contains(qq)
                | df_f["descricao"].astype(str).str.lower().str.contains(qq)
            ]

        total_rows = int(len(df_f))
        total_pages = max(1, (total_rows + page_size - 1) // page_size)

        # ajusta página se mudou filtro
        if st.session_state["page"] > total_pages:
            st.session_state["page"] = 1

        pcol1, pcol2, pcol3 = st.columns([0.33, 0.34, 0.33])
        with pcol1:
            if st.button("⬅️", use_container_width=True, disabled=(st.session_state["page"] <= 1)):
                st.session_state["page"] -= 1
                st.rerun()
        with pcol2:
            st.caption(f"Página {st.session_state['page']} / {total_pages} • {total_rows} itens")
        with pcol3:
            if st.button("➡️", use_container_width=True, disabled=(st.session_state["page"] >= total_pages)):
                st.session_state["page"] += 1
                st.rerun()

        start = (st.session_state["page"] - 1) * page_size
        end = start + page_size
        page_df = df_f.iloc[start:end].reset_index(drop=True)

        grid_cols = st.columns(2, gap="small")

        # 🔥 widget-count menor (poucos cards por vez) = app mais leve
        for i, r in enumerate(page_df.itertuples(index=False)):
            codigo = str(r.codigo).zfill(7)
            descricao = str(r.descricao)
            preco = float(r.preco)

            col = grid_cols[i % 2]
            with col:
                card = st.container(border=True)
                with card:
                    st.markdown('<div class="card-inner">', unsafe_allow_html=True)
                    st.markdown(f'<div class="product-sku">SKU {codigo}</div>', unsafe_allow_html=True)
                    st.markdown(f'<div class="product-desc">{descricao}</div>', unsafe_allow_html=True)
                    st.markdown(f'<div class="product-price">{brl(preco)}</div>', unsafe_allow_html=True)

                    # qty por card (ok porque page_size é pequeno)
                    qtd = st.number_input(
                        "Qtd",
                        value=1,
                        min_value=1,
                        step=1,
                        key=f"qtd_{codigo}_{start+i}",
                    )

                    if st.button(
                        "Adicionar ➕",
                        key=f"add_{codigo}_{start+i}",
                        type="primary",
                        use_container_width=True,
                    ):
                        cart_add(codigo=codigo, qtd=int(qtd), preco=float(preco))
                        st.toast(f"Adicionado {codigo} x{int(qtd)}", icon="✅")
                        st.rerun()

                    st.markdown("</div>", unsafe_allow_html=True)

    with col_right:
        st.markdown("### 🛒 Carrinho")

        cart_list = st.session_state["carrinho"]

        if cart_list:
            df_cart = pd.DataFrame(cart_list).copy()
            df_cart["codigo"] = df_cart["codigo"].astype(str).str.zfill(7)

            # join rápido por map (evita merge pesado em loop grande)
            df_cart["descricao"] = df_cart["codigo"].map(lambda c: produtos_map.get(c, ("", 0.0))[0])
            df_cart["descricao"] = df_cart["descricao"].fillna("")

            df_cart = df_cart[["codigo", "descricao", "qtd", "preco", "total"]]
            df_cart["qtd"] = df_cart["qtd"].astype(int)
            df_cart["preco"] = df_cart["preco"].astype(float)
            df_cart["total"] = df_cart["total"].astype(float)

            st.dataframe(df_cart, use_container_width=True, height=330)

            total = float(df_cart["total"].sum())
            st.metric("Total do Pedido", brl(total))

            c1, c2 = st.columns(2)
            with c1:
                if st.button("🧹 Limpar carrinho", use_container_width=True):
                    cart_clear()
                    st.rerun()

            with c2:
                if st.button("✅ Gerar QR do pedido", use_container_width=True, type="primary"):
                    order_id = uuid.uuid4().hex[:10].upper()
                    payload = build_order_payload_min(
                        cliente=st.session_state["cliente"],
                        carrinho_list=cart_list,
                        order_id=order_id,
                    )
                    qr_text = encode_qr_payload(payload)
                    st.session_state["last_qr_text"] = qr_text
                    st.session_state["last_qr_png"] = make_qr_png(qr_text)
                    st.rerun()

        else:
            st.info("Carrinho vazio. Adicione produtos no catálogo.")

        if st.session_state.get("last_qr_png"):
            st.divider()
            st.success("QR gerado! Leia no Desktop para reconstruir o pedido.")
            st.image(st.session_state["last_qr_png"], width=520)
            with st.expander("Ver conteúdo do QR (compactado)"):
                st.code(st.session_state["last_qr_text"] or "")

# =========================================================
# DESKTOP
# =========================================================
with tab_desktop:
    st.subheader("💻 Desktop — ler QR e reproduzir pedido")

    # estados do scanner/import
    st.session_state.setdefault("scan_token", 0)
    st.session_state.setdefault("import_ok", False)
    st.session_state.setdefault("import_pedido", None)
    st.session_state.setdefault("imported_token", -1)

    def arm_new_scan():
        st.session_state["scan_token"] += 1
        st.session_state["import_ok"] = False
        st.session_state["import_pedido"] = None
        st.session_state["imported_token"] = -1
        st.rerun()

    colA, colB = st.columns([1, 1], gap="large")

    with colA:
        st.markdown("### Leitor de QR Code")

        c1, c2 = st.columns([0.45, 0.55])
        with c1:
            if st.button("🆕 Novo QR", use_container_width=True, type="primary"):
                arm_new_scan()
        with c2:
            st.caption("Câmera aberta • Captura automática • Debounce • Multi-scale")

        st.markdown(
            """
            <style>
            video {
                width: 100% !important;
                max-width: 840px !important;
                height: 480px !important;
                object-fit: cover !important;
                border-radius: 12px;
                background: #000;
            }
            </style>
            """,
            unsafe_allow_html=True,
        )

        camera_mode_label = st.selectbox(
            "Escolha a câmera",
            ["Traseira (recomendada)", "Frontal"],
            index=0,
            key="camera_select",
        )
        facing_mode = "environment" if camera_mode_label.startswith("Traseira") else "user"

        RTC_CONFIGURATION = RTCConfiguration(
            {"iceServers": [{"urls": ["stun:stun.l.google.com:19302"]}]}
        )

        # constraints agressivas (browser pode ignorar parte)
        media_constraints = {
            "video": {
                "facingMode": facing_mode,
                "width": {"ideal": 1920, "min": 1280},
                "height": {"ideal": 1080, "min": 720},
                # fps menor geralmente melhora nitidez e reduz blur
                "frameRate": {"ideal": 20, "max": 24},
                "advanced": [
                    {"focusMode": "continuous"},
                    {"exposureMode": "continuous"},
                    {"whiteBalanceMode": "continuous"},
                ],
            },
            "audio": False,
        }

        ctx = webrtc_streamer(
            key="qr-reader",
            video_processor_factory=QRVideoProcessor,
            rtc_configuration=RTC_CONFIGURATION,
            media_stream_constraints=media_constraints,
            async_processing=True,
        )

        # mantém UI atualizando enquanto não importou
        if not st.session_state["import_ok"]:
            st_autorefresh(interval=220, key=f"poll_scan_{st.session_state['scan_token']}")

        if ctx.video_processor:
            ctx.video_processor.scan_token = st.session_state["scan_token"]
            st.info(ctx.video_processor.last_status)

            # importação automática
            if (
                ctx.video_processor.last_text
                and st.session_state["imported_token"] != st.session_state["scan_token"]
            ):
                qr_text = ctx.video_processor.last_text

                try:
                    meta = decode_qr_payload(qr_text)
                except Exception as e:
                    st.error(f"Não consegui decodificar o QR: {e}")
                    meta = None

                if meta and isinstance(meta, dict) and meta.get("type") == "ORDER_MIN":
                    st.session_state["import_ok"] = True
                    st.session_state["import_pedido"] = meta
                    st.session_state["imported_token"] = st.session_state["scan_token"]
                    st.rerun()

    with colB:
        st.markdown("### Pedido reproduzido")

        pedido = st.session_state.get("import_pedido")
        if st.session_state.get("import_ok") and pedido:
            st.success("✅ Pedido reproduzido com sucesso")

            st.markdown("**Cliente (mínimo)**")
            st.json(pedido.get("cliente", {}))

            itens_min = pd.DataFrame(pedido.get("itens", [])).rename(
                columns={"c": "codigo", "q": "qtde", "p": "preco_unit"}
            )

            if itens_min.empty:
                st.warning("Pedido sem itens.")
            else:
                itens_min["codigo"] = itens_min["codigo"].astype(str).str.zfill(7)
                itens_min["qtde"] = itens_min["qtde"].astype(int)
                itens_min["preco_unit"] = itens_min["preco_unit"].astype(float)

                # lookup mais rápido que merge (e evita NaN surpresa)
                itens_min["descricao"] = itens_min["codigo"].map(
                    lambda c: produtos_map.get(c, ("**PRODUTO NÃO ENCONTRADO NA TAB_PRODUTO**", 0.0))[0]
                )
                itens_min["subtotal"] = itens_min["qtde"] * itens_min["preco_unit"]

                st.markdown("**Itens**")
                st.dataframe(
                    itens_min[["codigo", "descricao", "qtde", "preco_unit", "subtotal"]],
                    use_container_width=True,
                    height=320,
                )
                st.metric("Total", brl(float(itens_min["subtotal"].sum())))
        else:
            st.info("Aguardando leitura do QR para reproduzir o pedido.")
