import streamlit as st
import ifcopenshell
from ifctester import ids, reporter
import tempfile
import os
import glob
import re

# ── Sidhuvud ──────────────────────────────────────────────────────────────────
st.set_page_config(page_title="IFC Bouncer 🚪", page_icon="🚪", layout="wide")
st.title("🚪 IFC Bouncer")
st.caption("Ladda upp din IFC-fil och välj vilka IDS-regler du vill validera mot.")

# ── Filnamnskontroll ──────────────────────────────────────────────────────────
def check_filename(filename):
    pattern = r"^[A-Za-z]-\d{2}-V-\d{2,4}\.ifc$"
    return re.match(pattern, filename) is not None

# ── Hitta IDS-filer i repot ───────────────────────────────────────────────────
ids_folder = os.path.join(os.path.dirname(__file__), "ids")
available_ids = sorted(glob.glob(os.path.join(ids_folder, "*.ids")))
ids_names = [os.path.basename(f) for f in available_ids]

# ── Sidebar: välj IDS-filer ───────────────────────────────────────────────────
st.sidebar.header("⚙️ Inställningar")

if not ids_names:
    st.sidebar.warning("Inga IDS-filer hittades i mappen /ids/")
    selected_ids = []
else:
    selected_ids = st.sidebar.multiselect(
        "Välj IDS-filer att validera mot:",
        options=ids_names,
        default=ids_names
    )

# ── Ladda upp IFC-fil ─────────────────────────────────────────────────────────
uploaded_ifc = st.file_uploader("Ladda upp IFC-fil", type=["ifc"])
st.caption("Filnamnet måste följa formatet: `D-SS-V-XYZA.ifc` — t.ex. `A-40-V-1234.ifc` - Disciplin-SS-V-Byggnadsdel enl projektdefinitioner")

# ── Filnamnskontroll ──────────────────────────────────────────────────────────
if uploaded_ifc:
    if not check_filename(uploaded_ifc.name):
        st.error(
            f"❌ Filnamnet följer inte namnkonventionen: `{uploaded_ifc.name}`\n\n"
            f"Förväntat format: `A-40-V-0000.ifc`"
        )
        st.stop()
    else:
        st.success(f"✅ Filnamnet är korrekt: `{uploaded_ifc.name}`")

# ── Kör validering ────────────────────────────────────────────────────────────
if uploaded_ifc and selected_ids:
    if st.button("🚪 Kör validering", type="primary"):

        with tempfile.NamedTemporaryFile(suffix=".ifc", delete=False) as tmp:
            tmp.write(uploaded_ifc.read())
            tmp_ifc_path = tmp.name

        try:
            ifc_model = ifcopenshell.open(tmp_ifc_path)
        except Exception as e:
            st.error(f"Kunde inte läsa IFC-filen: {e}")
            os.unlink(tmp_ifc_path)
            st.stop()

        st.divider()
        st.subheader("📋 Valideringsresultat")

        for ids_name in selected_ids:
            ids_path = os.path.join(ids_folder, ids_name)
            try:
                specs = ids.open(ids_path)
                specs.validate(ifc_model)

                total = len(specs.specifications)
                passed = sum(1 for s in specs.specifications if s.status)
                failed = total - passed

                with st.expander(
                    f"{'✅' if failed == 0 else '❌'} {ids_name}  —  "
                    f"{passed}/{total} krav godkända",
                    expanded=(failed > 0)
                ):
                    for spec in specs.specifications:
                        icon = "✅" if spec.status else "❌"
                        st.markdown(f"**{icon} {spec.name}**")
                        if not spec.status:
                            for req in spec.requirements:
                                for result in (req.failed_entities or []):
                                    st.caption(f"  → {result}")

            except Exception as e:
                st.error(f"Fel vid validering av {ids_name}: {e}")

        os.unlink(tmp_ifc_path)

elif uploaded_ifc and not selected_ids:
    st.warning("Välj minst en IDS-fil i sidopanelen.")
elif not uploaded_ifc and selected_ids:
    st.info("Ladda upp en IFC-fil för att köra validering.")
