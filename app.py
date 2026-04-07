import streamlit as st
import ifcopenshell
import ifcopenshell.util.element
from ifctester import ids, reporter
import tempfile
import os
import glob
import re
import json
import io
import sys
import traceback
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime
from bcf.v2.bcfxml import BcfXml
from bcf.v2 import model as mdl

# ── Config ────────────────────────────────────────────────────────────────────
IDS_FOLDER = Path("ids")
APP_TITLE = "IFC Bouncer"

# ── Sidhuvud ──────────────────────────────────────────────────────────────────
st.set_page_config(page_title="IFC Bouncer 🚪", page_icon="🚪", layout="wide")

# ── Filnamnskontroll ──────────────────────────────────────────────────────────
def check_filename(filename):
    pattern = r"^[A-Za-z]-\d{2}-V-\d{2,4}\.ifc$"
    return re.match(pattern, filename) is not None


# ── Undantagshantering ────────────────────────────────────────────────────────
def load_exceptions(uploaded_exc):
    """Laddar undantag från CSV eller Excel. Returnerar dict med (TypeID, Rule) som nyckel."""
    exceptions = {}
    if uploaded_exc is None:
        return exceptions
    try:
        if uploaded_exc.name.endswith(".csv"):
            df = pd.read_csv(uploaded_exc)
        else:
            df = pd.read_excel(uploaded_exc)
        df.columns = [c.strip() for c in df.columns]
        for _, row in df.iterrows():
            type_id = str(row.get("TypeID", "")).strip()
            rule = str(row.get("Rule", "*")).strip()
            if type_id:
                key = (type_id, rule)
                exceptions[key] = {
                    "approved_by": str(row.get("ApprovedBy", "")),
                    "date": str(row.get("Date", "")),
                    "reference": str(row.get("Reference", "")),
                    "reason": str(row.get("Reason", "")),
                }
    except Exception as e:
        st.sidebar.error(f"Kunde inte ladda undantagsfil: {e}")
    return exceptions


def is_excepted(entity, rule_name, exceptions):
    """Kontrollerar om ett misslyckat element täcks av ett undantag."""
    if not exceptions:
        return False, None
    psets = ifcopenshell.util.element.get_psets(entity)
    jm = psets.get("JM", {})
    type_id = jm.get("TypeID", "")
    if not type_id:
        return False, None
    key = (type_id, rule_name)
    if key in exceptions:
        return True, exceptions[key]
    key_wild = (type_id, "*")
    if key_wild in exceptions:
        return True, exceptions[key_wild]
    return False, None


# ── BCF-hjälpfunktion ─────────────────────────────────────────────────────────
def add_bcf_viewpoint(topic, issue, ifc_file):
    first_entity = issue.get("first_entity")
    guids = issue.get("guids", [])
    if first_entity is not None and hasattr(first_entity, 'ObjectPlacement') and first_entity.ObjectPlacement:
        viewpoint = topic.add_viewpoint(first_entity)
        if len(guids) > 1:
            vi = viewpoint.visualization_info
            if vi.components and vi.components.selection:
                existing_guids = {c.ifc_guid for c in vi.components.selection.component}
                for guid in guids:
                    if guid not in existing_guids:
                        vi.components.selection.component.append(mdl.Component(ifc_guid=guid))
    elif guids:
        fallback_entity = None
        if ifc_file is not None:
            try:
                fallback_entity = ifc_file.by_guid(guids[0])
            except Exception:
                pass
        if fallback_entity is not None and hasattr(fallback_entity, 'ObjectPlacement') and fallback_entity.ObjectPlacement:
            viewpoint = topic.add_viewpoint(fallback_entity)
            if len(guids) > 1:
                vi = viewpoint.visualization_info
                if vi.components and vi.components.selection:
                    existing_guids = {c.ifc_guid for c in vi.components.selection.component}
                    for guid in guids:
                        if guid not in existing_guids:
                            vi.components.selection.component.append(mdl.Component(ifc_guid=guid))
        else:
            topic.add_viewpoint_from_point_and_guids(np.array([0.0, 0.0, 5.0]), *guids)


def get_pset_value(element, pset_name, prop_name):
    psets = ifcopenshell.util.element.get_psets(element)
    return psets.get(pset_name, {}).get(prop_name)


def get_type_id(entity):
    psets = ifcopenshell.util.element.get_psets(entity)
    return psets.get("JM", {}).get("TypeID", "—")


# ── Ladda IDS-filer ───────────────────────────────────────────────────────────
def load_ids_files():
    ids_files = {}
    if IDS_FOLDER.exists():
        for f in sorted(IDS_FOLDER.glob("*.ids")):
            try:
                ids_obj = ids.open(str(f))
                ids_files[f.stem] = {"path": f, "ids": ids_obj}
            except Exception as e:
                # Samla ALL tillgänglig felinformation
                parts = [f"**Kunde inte ladda {f.name}:** {e}"]

                # Visa underliggande orsak (xml_error, __cause__)
                for attr in ("xml_error", "reason", "__cause__"):
                    val = getattr(e, attr, None)
                    if val:
                        parts.append(f"**{attr}:** `{val}`")

                # Visa fullständig traceback
                parts.append(f"```\n{traceback.format_exc()}\n```")

                st.warning("\n\n".join(parts))
    else:
        st.error(f"Mappen {IDS_FOLDER} finns inte!")
    return ids_files


# ── Huvudapp ──────────────────────────────────────────────────────────────────
st.title("🚪 IFC Bouncer")
st.caption("Ladda upp din IFC-fil och välj vilka IDS-regler du vill validera mot.")

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.header("⚙️ Inställningar")
    st.markdown("---")

    st.markdown("**Undantag**")
    uploaded_exc = st.file_uploader(
        "Ladda upp undantagsfil",
        type=["csv", "xlsx"],
        help="CSV eller Excel med kolumnerna: TypeID, Rule, ApprovedBy, Date, Reference, Reason"
    )
    exceptions = load_exceptions(uploaded_exc)
    if exceptions:
        st.success(f"{len(exceptions)} undantag inlästa")

    st.markdown("---")
    st.markdown("**Så här gör du:**")
    st.markdown(
        "1. Ladda upp din IFC-fil\n"
        "2. Ladda ev. upp undantagsfil\n"
        "3. Välj regelset\n"
        "4. Klicka **Kör validering**\n"
        "5. Granska resultaten"
    )
    st.markdown("---")

# ── IDS-filer och val ─────────────────────────────────────────────────────────
ids_files = load_ids_files()

if not ids_files:
    st.error("Inga IDS-filer hittades i mappen /ids/.")
else:
    st.subheader("Välj regelset")
    selected_ids = []
    cols = st.columns(2)
    for i, (name, data) in enumerate(ids_files.items()):
        col = cols[i % 2]
        with col:
            title = name.replace("_", " ")
            if st.checkbox(title, value=True):
                selected_ids.append((name, data))

# ── Filuppladdning ────────────────────────────────────────────────────────────
st.markdown("---")
uploaded_ifc = st.file_uploader("Ladda upp IFC-fil", type=["ifc"])
st.caption("Filnamnet måste följa formatet: `A-40-V-0000.ifc` — t.ex. `A-40-V-1234.ifc`")

# ── Filnamnskontroll ──────────────────────────────────────────────────────────
if uploaded_ifc:
    if not check_filename(uploaded_ifc.name):
        st.error(
            f"❌ Filnamnet följer inte namnkonventionen: {uploaded_ifc.name}\n\n"
            "Förväntat format: A-40-V-0000.ifc"
        )
        st.stop()
    else:
        st.success(f"✅ Filnamnet är korrekt: {uploaded_ifc.name}")
``
# ── Val av kontroller (valsteg före validering) ────────────────────────────────
st.markdown("---")
st.subheader("🧪 Välj vilka kontroller som ska köras")

col1, col2 = st.columns(2)
with col1:
    run_storeys = st.checkbox("Rätt våningsplan", value=True)
    run_storey_heights = st.checkbox("Max två våningshöjder", value=True)
    run_spaces = st.checkbox("Rum / Spaces", value=True)
    run_windows = st.checkbox("Fönster", value=True)
with col2:
    run_doors = st.checkbox("Dörrar", value=True)
    run_pset_jm = st.checkbox("PropertySet JM", value=True)
    run_pset_common = st.checkbox("Pset_*Common", value=True)
    run_bq = st.checkbox("BaseQuantities", value=True)

: `{uploaded_ifc.name}`)

# ── Kör-knapp ─────────────────────────────────────────────────────────────────
st.markdown("---")
run_button = st.button("🚪 Kör validering", type="primary", disabled=uploaded_ifc is None)

if run_button and uploaded_ifc is not None:
    with tempfile.NamedTemporaryFile(suffix=".ifc", delete=False) as tmp:
        tmp.write(uploaded_ifc.getvalue())
        tmp_path = tmp.name

    try:
        with st.spinner("Da bouncer bouncar... 🚪"):
            ifc_file = ifcopenshell.open(tmp_path)

        st.success(
            f"Inläst: **{uploaded_ifc.name}** — "
            f"Schema: {ifc_file.schema}, "
            f"Element: {len(list(ifc_file))}"
        )

        all_results = []
        bcf_issues = []
        new_exceptions = []

        # ── IDS-validering ────────────────────────────────────────────────────
        for name, data in selected_ids:
            ids_obj = ids.open(str(data["path"]))
            with st.spinner(f"Kontrollerar: {name.replace('_', ' ')}..."):
                ids_obj.validate(ifc_file)

            st.subheader(f"📋 {name.replace('_', ' ')}")

            for spec in ids_obj.specifications:
                applicable = spec.applicable_entities if spec.applicable_entities else []
                total = len(applicable)
                failed = spec.failed_entities if spec.failed_entities else set()

                if spec.status is True:
                    st.markdown(f"✅ **{spec.name}** — {total} element kontrollerade, alla godkända")
                elif spec.status is False:
                    real_failures = {}
                    excepted_items = {}

                    for req in spec.requirements:
                        if hasattr(req, 'failures') and req.failures:
                            for failure in req.failures:
                                if isinstance(failure, dict):
                                    entity = failure.get("element") or failure.get("entity")
                                    reason = failure.get("reason", "Okänd")
                                else:
                                    entity = getattr(failure, 'element', None) or getattr(failure, 'entity', None)
                                    reason = getattr(failure, 'reason', "Okänd")
                                if entity is None:
                                    continue
                                eid = entity.id()
                                entity_name = entity.Name if hasattr(entity, 'Name') and entity.Name else "—"
                                type_id = get_type_id(entity)
                                is_exc, exc_info = is_excepted(entity, spec.name, exceptions)
                                item = {
                                    "type": entity.is_a(),
                                    "name": entity_name,
                                    "type_id": type_id,
                                    "reasons": [],
                                    "entity": entity,
                                }
                                if is_exc:
                                    if eid not in excepted_items:
                                        excepted_items[eid] = {**item, "exception": exc_info}
                                    excepted_items[eid]["reasons"].append(str(reason))
                                else:
                                    if eid not in real_failures:
                                        real_failures[eid] = item
                                    real_failures[eid]["reasons"].append(str(reason))

                    real_count = len(real_failures)
                    exc_count = len(excepted_items)

                    if real_count > 0:
                        with st.expander(
                            f"❌ **{spec.name}** — {real_count} misslyckade"
                            f"{f', {exc_count} godkända undantag' if exc_count else ''}",
                            expanded=False
                        ):
                            rows = []
                            for eid, info in sorted(real_failures.items()):
                                rows.append({
                                    "ID": f"#{eid}",
                                    "Typ": info["type"],
                                    "Namn": info["name"],
                                    "TypeID": info["type_id"],
                                    "Anledning": "; ".join(info["reasons"][:3]),
                                })
                                if info["type_id"] != "—":
                                    new_exceptions.append({
                                        "TypeID": info["type_id"],
                                        "Rule": spec.name,
                                        "ElementName": info["name"],
                                        "ApprovedBy": "",
                                        "Date": "",
                                        "Reference": "",
                                        "Reason": "",
                                    })
                            st.dataframe(rows, use_container_width=True, hide_index=True)

                        guids = []
                        first_entity = None
                        for eid, info in real_failures.items():
                            guid = getattr(info["entity"], 'GlobalId', None)
                            if guid:
                                guids.append(guid)
                                if first_entity is None:
                                    first_entity = info["entity"]
                        if guids:
                            bcf_issues.append({
                                "title": f"{name}: {spec.name}",
                                "description": f"{real_count} element misslyckades (exkl. {exc_count} undantag).",
                                "guids": guids,
                                "first_entity": first_entity,
                            })

                    if exc_count > 0:
                        with st.expander(f"⚠️ **Godkända undantag** för {spec.name} — {exc_count} st", expanded=False):
                            exc_rows = []
                            for eid, info in sorted(excepted_items.items()):
                                exc = info["exception"]
                                exc_rows.append({
                                    "ID": f"#{eid}",
                                    "TypeID": info["type_id"],
                                    "Namn": info["name"],
                                    "Godkänd av": exc.get("approved_by", ""),
                                    "Referens": exc.get("reference", ""),
                                    "Anledning": exc.get("reason", ""),
                                })
                            st.dataframe(exc_rows, use_container_width=True, hide_index=True)

                    fail_status = "FAIL" if real_count > 0 else "PASS"
                else:
                    st.markdown(f"⚠️ **{spec.name}** — Inga tillämpliga element hittades")
                    fail_status = "N/A"
                    real_count = 0

                all_results.append({
                    "rule_set": name,
                    "rule": spec.name,
                    "status": "PASS" if spec.status is True else fail_status,
                    "elements_checked": total,
                })

        # ── Avancerade kontroller ─────────────────────────────────────────────
        st.markdown("---")
        st.subheader("🔍 Avancerade kontroller")

        # 1. Element tilldelade våningsplan
        unassigned = []
        for entity_type in ["IfcWall", "IfcDoor", "IfcWindow", "IfcSlab", "IfcColumn", "IfcBeam"]:
            for element in ifc_file.by_type(entity_type):
                container = ifcopenshell.util.element.get_container(element)
                if container is None:
                    unassigned.append(element)
        if unassigned:
            type_counts = {}
            for e in unassigned:
                t = e.is_a()
                type_counts[t] = type_counts.get(t, 0) + 1
            summary_text = ", ".join(f"{c}x {t}" for t, c in type_counts.items())
            with st.expander(f"❌ **Element utan våningsplan** — {len(unassigned)} st", expanded=False):
                rows = [{"ID": f"#{e.id()}", "Typ": e.is_a(), "Namn": getattr(e, 'Name', None) or "—", "TypeID": get_type_id(e)} for e in unassigned[:50]]
                st.dataframe(rows, use_container_width=True, hide_index=True)
            guids = [e.GlobalId for e in unassigned if e.GlobalId][:50]
            if guids:
                bcf_issues.append({
                    "title": f"{len(unassigned)} element utan våningsplan",
                    "description": summary_text,
                    "guids": guids,
                    "first_entity": unassigned[0],
                })
            all_results.append({"rule_set": "Avancerat", "rule": "Element tilldelade våningsplan", "status": "FAIL", "elements_checked": len(unassigned)})
        else:
            st.markdown("✅ **Element tilldelade våningsplan** — alla element tilldelade ett våningsplan")
            all_results.append({"rule_set": "Avancerat", "rule": "Element tilldelade våningsplan", "status": "PASS", "elements_checked": 0})

        # 2. Element spänner över max två våningshöjder
        storeys = sorted(ifc_file.by_type("IfcBuildingStorey"), key=lambda s: s.Elevation or 0)
        storey_elevations = [(s.Name or f"Våning {i}", s.Elevation or 0) for i, s in enumerate(storeys)]
        multi_storey_issues = []
        if len(storeys) >= 2:
            max_span = storey_elevations[-1][1] - storey_elevations[0][1]
            two_storey_height = None
            if len(storeys) >= 3:
                two_storey_height = storey_elevations[2][1] - storey_elevations[0][1]
            else:
                two_storey_height = max_span

            for entity_type in ["IfcWall", "IfcColumn"]:
                for element in ifc_file.by_type(entity_type):
                    try:
                        bb = ifcopenshell.util.element.get_psets(element)
                        placement = getattr(element, 'ObjectPlacement', None)
                        if placement is None:
                            continue
                        qtos = ifcopenshell.util.element.get_psets(element, qtos_only=True)
                        height = None
                        for qto in qtos.values():
                            height = qto.get("Height") or qto.get("Length") or height
                        if height and two_storey_height and height > two_storey_height * 1.1:
                            multi_storey_issues.append({
                                "ID": f"#{element.id()}",
                                "Typ": element.is_a(),
                                "Namn": getattr(element, 'Name', None) or "—",
                                "TypeID": get_type_id(element),
                                "Höjd (m)": f"{height:.2f}",
                                "Max tillåtet (m)": f"{two_storey_height:.2f}",
                                "_guid": element.GlobalId,
                                "_entity": element,
                            })
                    except Exception:
                        continue

        if multi_storey_issues:
            with st.expander(f"❌ **Element spänner över mer än två våningar** — {len(multi_storey_issues)} st", expanded=False):
                st.dataframe([{k: v for k, v in r.items() if not k.startswith("_")} for r in multi_storey_issues], use_container_width=True, hide_index=True)
            guids = [r["_guid"] for r in multi_storey_issues if r["_guid"]]
            if guids:
                bcf_issues.append({
                    "title": f"{len(multi_storey_issues)} element spänner över mer än två våningar",
                    "description": "Element vars höjd överstiger två våningshöjder",
                    "guids": guids,
                    "first_entity": multi_storey_issues[0]["_entity"],
                })
            all_results.append({"rule_set": "Avancerat", "rule": "Max två våningshöjder", "status": "FAIL", "elements_checked": len(multi_storey_issues)})
        else:
            st.markdown("✅ **Max två våningshöjder** — inga element spänner över mer än två våningar")
            all_results.append({"rule_set": "Avancerat", "rule": "Max två våningshöjder", "status": "PASS", "elements_checked": 0})

        # 3. Rum/spaces finns och har namn och golvyta
        spaces = ifc_file.by_type("IfcSpace")
        if len(spaces) == 0:
            st.markdown("❌ **Rum/Spaces** — inga IfcSpace hittades i modellen")
            bcf_issues.append({
                "title": "Inga rum/spaces i modellen",
                "description": "Noll IfcSpace-element hittades.",
                "guids": [],
                "first_entity": None,
            })
            all_results.append({"rule_set": "Avancerat", "rule": "Rum/spaces finns", "status": "FAIL", "elements_checked": 0})
        else:
            no_area = []
            unnamed = []
            for space in spaces:
                area = get_pset_value(space, "Qto_SpaceBaseQuantities", "NetFloorArea")
                if area is None or area <= 0:
                    no_area.append(space)
                if not space.Name or space.Name.strip() == "":
                    unnamed.append(space)
            space_issues = []
            if no_area:
                space_issues.append(f"{len(no_area)} utan NetFloorArea")
            if unnamed:
                space_issues.append(f"{len(unnamed)} utan namn")
            if space_issues:
                with st.expander(f"❌ **Rum/Spaces** — {len(spaces)} hittade, problem: {', '.join(space_issues)}", expanded=False):
                    if no_area:
                        st.markdown(f"**Saknar NetFloorArea:** {len(no_area)} rum")
                        rows = [{"ID": f"#{s.id()}", "Namn": s.Name or "—", "LongName": getattr(s, 'LongName', None) or "—"} for s in no_area[:20]]
                        st.dataframe(rows, use_container_width=True, hide_index=True)
                    if unnamed:
                        st.markdown(f"**Saknar namn:** {len(unnamed)} rum")
                        rows = [{"ID": f"#{s.id()}"} for s in unnamed[:20]]
                        st.dataframe(rows, use_container_width=True, hide_index=True)
                problem_spaces = list(set(no_area + unnamed))
                guids = [s.GlobalId for s in problem_spaces if s.GlobalId][:30]
                if guids:
                    bcf_issues.append({
                        "title": f"Rum med problem: {', '.join(space_issues)}",
                        "description": f"{len(spaces)} rum totalt",
                        "guids": guids,
                        "first_entity": problem_spaces[0],
                    })
                all_results.append({"rule_set": "Avancerat", "rule": "Rum/spaces fullständiga", "status": "FAIL", "elements_checked": len(spaces)})
            else:
                st.markdown(f"✅ **Rum/Spaces** — {len(spaces)} rum, alla med namn och golvyta")
                all_results.append({"rule_set": "Avancerat", "rule": "Rum/spaces fullständiga", "status": "PASS", "elements_checked": len(spaces)})

        # 4. Fönster finns och sitter i väggar
        windows = ifc_file.by_type("IfcWindow")
        if len(windows) == 0:
            st.markdown("❌ **Fönster** — inga IfcWindow hittades (trolig exportinställning saknas)")
            bcf_issues.append({"title": "Inga fönster i modellen", "description": "Noll IfcWindow-element.", "guids": [], "first_entity": None})
            all_results.append({"rule_set": "Avancerat", "rule": "Fönster finns och är värdbaserade", "status": "FAIL", "elements_checked": 0})
        else:
            orphan_windows = [w for w in windows if not (hasattr(w, "FillsVoids") and w.FillsVoids)]
            if orphan_windows:
                with st.expander(f"❌ **Fönster** — {len(orphan_windows)}/{len(windows)} sitter inte i vägg", expanded=False):
                    rows = [{"ID": f"#{w.id()}", "Namn": getattr(w, 'Name', None) or "—", "TypeID": get_type_id(w)} for w in orphan_windows[:20]]
                    st.dataframe(rows, use_container_width=True, hide_index=True)
                guids = [w.GlobalId for w in orphan_windows if w.GlobalId][:30]
                if guids:
                    bcf_issues.append({"title": f"{len(orphan_windows)} fönster utan värd-vägg", "description": "Saknar IfcRelFillsElement", "guids": guids, "first_entity": orphan_windows[0]})
                all_results.append({"rule_set": "Avancerat", "rule": "Fönster finns och är värdbaserade", "status": "FAIL", "elements_checked": len(windows)})
            else:
                st.markdown(f"✅ **Fönster** — {len(windows)} fönster, alla sitter i väggar")
                all_results.append({"rule_set": "Avancerat", "rule": "Fönster finns och är värdbaserade", "status": "PASS", "elements_checked": len(windows)})

        # 5. Dörrar sitter i väggar
        doors = ifc_file.by_type("IfcDoor")
        if doors:
            orphan_doors = [d for d in doors if not (hasattr(d, "FillsVoids") and d.FillsVoids)]
            if orphan_doors:
                with st.expander(f"❌ **Dörrar** — {len(orphan_doors)}/{len(doors)} sitter inte i vägg", expanded=False):
                    rows = [{"ID": f"#{d.id()}", "Namn": getattr(d, 'Name', None) or "—", "TypeID": get_type_id(d)} for d in orphan_doors[:20]]
                    st.dataframe(rows, use_container_width=True, hide_index=True)
                guids = [d.GlobalId for d in orphan_doors if d.GlobalId][:30]
                if guids:
                    bcf_issues.append({"title": f"{len(orphan_doors)} dörrar utan värd-vägg", "description": "Saknar IfcRelFillsElement", "guids": guids, "first_entity": orphan_doors[0]})
                all_results.append({"rule_set": "Avancerat", "rule": "Dörrar värdbaserade", "status": "FAIL", "elements_checked": len(doors)})
            else:
                st.markdown(f"✅ **Dörrar** — {len(doors)} dörrar, alla sitter i väggar")
                all_results.append({"rule_set": "Avancerat", "rule": "Dörrar värdbaserade", "status": "PASS", "elements_checked": len(doors)})
        else:
            st.markdown("⚠️ **Dörrar** — inga IfcDoor hittades i modellen")
            all_results.append({"rule_set": "Avancerat", "rule": "Dörrar värdbaserade", "status": "N/A", "elements_checked": 0})

        # 6. Propertyset JM finns
        element_types = ["IfcWall", "IfcDoor", "IfcWindow", "IfcSlab", "IfcColumn", "IfcBeam"]
        missing_jm = []
        for entity_type in element_types:
            for element in ifc_file.by_type(entity_type):
                psets = ifcopenshell.util.element.get_psets(element)
                if "JM" not in psets:
                    missing_jm.append(element)
        if missing_jm:
            with st.expander(f"❌ **Propertyset JM saknas** — {len(missing_jm)} element", expanded=False):
                rows = [{"ID": f"#{e.id()}", "Typ": e.is_a(), "Namn": getattr(e, 'Name', None) or "—"} for e in missing_jm[:50]]
                st.dataframe(rows, use_container_width=True, hide_index=True)
            guids = [e.GlobalId for e in missing_jm if e.GlobalId][:50]
            if guids:
                bcf_issues.append({"title": f"{len(missing_jm)} element saknar propertyset JM", "description": "JM-pset saknas", "guids": guids, "first_entity": missing_jm[0]})
            all_results.append({"rule_set": "Avancerat", "rule": "Propertyset JM finns", "status": "FAIL", "elements_checked": len(missing_jm)})
        else:
            st.markdown("✅ **Propertyset JM** — finns på alla kontrollerade element")
            all_results.append({"rule_set": "Avancerat", "rule": "Propertyset JM finns", "status": "PASS", "elements_checked": 0})

        # 7. Propertyset IfcCommon finns (Pset_WallCommon, Pset_DoorCommon etc.)
        common_pset_map = {
            "IfcWall": "Pset_WallCommon",
            "IfcDoor": "Pset_DoorCommon",
            "IfcWindow": "Pset_WindowCommon",
            "IfcSlab": "Pset_SlabCommon",
            "IfcColumn": "Pset_ColumnCommon",
            "IfcBeam": "Pset_BeamCommon",
        }
        missing_common = []
        for entity_type, pset_name in common_pset_map.items():
            for element in ifc_file.by_type(entity_type):
                psets = ifcopenshell.util.element.get_psets(element)
                if pset_name not in psets:
                    missing_common.append({"element": element, "expected_pset": pset_name})
        if missing_common:
            with st.expander(f"❌ **Pset_*Common saknas** — {len(missing_common)} element", expanded=False):
                rows = [{"ID": f"#{r['element'].id()}", "Typ": r['element'].is_a(), "Namn": getattr(r['element'], 'Name', None) or "—", "Förväntat pset": r['expected_pset']} for r in missing_common[:50]]
                st.dataframe(rows, use_container_width=True, hide_index=True)
            guids = [r['element'].GlobalId for r in missing_common if r['element'].GlobalId][:50]
            if guids:
                bcf_issues.append({"title": f"{len(missing_common)} element saknar Pset_*Common", "description": "IFC Common property sets saknas", "guids": guids, "first_entity": missing_common[0]["element"]})
            all_results.append({"rule_set": "Avancerat", "rule": "Pset_*Common finns", "status": "FAIL", "elements_checked": len(missing_common)})
        else:
            st.markdown("✅ **Pset_*Common** — finns på alla kontrollerade element")
            all_results.append({"rule_set": "Avancerat", "rule": "Pset_*Common finns", "status": "PASS", "elements_checked": 0})

        # 8. BaseQuantities PropertySet finns
        missing_base_quantities = []
        for element in ifc_file.by_type("IfcObject"):
            psets = ifcopenshell.util.element.get_psets(element)
            if "BaseQuantities" not in psets:
                missing_base_quantities.append({"element": element})
        
        if missing_base_quantities:
            with st.expander(f"❌ **BaseQuantities saknas** — {len(missing_base_quantities)} element", expanded=False):
                rows = [{"ID": f"#{r['element'].id()}", "Typ": r['element'].is_a(), "Namn": getattr(r['element'], 'Name', None) or "—"} for r in missing_base_quantities[:50]]
                st.dataframe(rows, use_container_width=True, hide_index=True)
            guids = [r['element'].GlobalId for r in missing_base_quantities if r['element'].GlobalId][:50]
            if guids:
                bcf_issues.append({"title": f"{len(missing_base_quantities)} element saknar BaseQuantities", "description": "PropertySet BaseQuantities saknas", "guids": guids, "first_entity": missing_base_quantities[0]["element"]})
            all_results.append({"rule_set": "Avancerat", "rule": "BaseQuantities PropertySet finns", "status": "FAIL", "elements_checked": len(missing_base_quantities)})
        else:
            st.markdown("✅ **BaseQuantities PropertySet** — finns för alla IfcObject")
            all_results.append({"rule_set": "Avancerat", "rule": "BaseQuantities PropertySet finns", "status": "PASS", "elements_checked": 0})

        # ── Sammanfattning ────────────────────────────────────────────────────
        st.markdown("---")
        st.subheader("📊 Sammanfattning")
        total_rules = len(all_results)
        passed = sum(1 for r in all_results if r["status"] == "PASS")
        failed_count = sum(1 for r in all_results if r["status"] == "FAIL")
        na = sum(1 for r in all_results if r["status"] == "N/A")

        col1, col2, col3 = st.columns(3)
        col1.metric("✅ Godkända", f"{passed}/{total_rules}")
        col2.metric("❌ Misslyckade", f"{failed_count}/{total_rules}")
        col3.metric("⚠️ Ej tillämpligt", f"{na}/{total_rules}")

        st.session_state.last_results = all_results
        st.session_state.last_filename = uploaded_ifc.name
        st.session_state.last_timestamp = datetime.now().isoformat()
        st.session_state.last_new_exceptions = new_exceptions

        # Generera BCF medan ifc_file fortfarande finns i minnet
        if bcf_issues:
            try:
                bcf_file = BcfXml.create_new("IFC Bouncer")
                for issue in bcf_issues:
                    topic = bcf_file.add_topic(
                        title=issue["title"],
                        description=issue["description"],
                        author="bim@jm.se",
                        topic_type="Error",
                        topic_status="Open",
                    )
                    if issue["guids"]:
                        try:
                            add_bcf_viewpoint(topic, issue, ifc_file)
                        except Exception:
                            pass
                bcf_path = tempfile.mktemp(suffix=".bcf")
                bcf_file.save(bcf_path)
                with open(bcf_path, "rb") as f:
                    st.session_state.last_bcf_bytes = f.read()
                os.unlink(bcf_path)
                st.session_state.last_bcf_count = len(bcf_issues)
            except Exception as e:
                st.session_state.last_bcf_bytes = None
                st.session_state.last_bcf_count = 0
                st.warning(f"BCF-generering misslyckades: {e}")
        else:
            st.session_state.last_bcf_bytes = None
            st.session_state.last_bcf_count = 0

    except Exception as e:
        st.error(f"Fel under validering: {str(e)}")
        st.exception(e)
    finally:
        os.unlink(tmp_path)

# ── Export ────────────────────────────────────────────────────────────────────
if "last_results" in st.session_state:
    st.markdown("---")
    st.subheader("📥 Exportera resultat")

    col_bcf, col_json, col_exc = st.columns(3)

    with col_bcf:
        bcf_bytes = st.session_state.get("last_bcf_bytes")
        bcf_count = st.session_state.get("last_bcf_count", 0)
        if bcf_bytes:
            st.download_button(
                "📋 BCF (Solibri/Revit)",
                data=bcf_bytes,
                file_name=f"bouncer_{st.session_state.last_filename}_{datetime.now().strftime('%Y%m%d_%H%M')}.bcf",
                mime="application/octet-stream",
            )
            st.caption(f"{bcf_count} ärenden")
        else:
            st.info("Inga fel — ingen BCF behövs. 🎉")

    with col_json:
        export_data = {
            "file": st.session_state.last_filename,
            "timestamp": st.session_state.last_timestamp,
            "results": st.session_state.last_results,
        }
        st.download_button(
            "📄 JSON-rapport",
            data=json.dumps(export_data, indent=2, ensure_ascii=False),
            file_name=f"bouncer_{st.session_state.last_filename}_{datetime.now().strftime('%Y%m%d_%H%M')}.json",
            mime="application/json",
        )

    with col_exc:
        new_exceptions = st.session_state.get("last_new_exceptions", [])
        if new_exceptions:
            seen = set()
            unique = []
            for exc in new_exceptions:
                key = (exc["TypeID"], exc["Rule"])
                if key not in seen:
                    seen.add(key)
                    unique.append(exc)
            df_exc = pd.DataFrame(unique, columns=["TypeID", "Rule", "ElementName", "ApprovedBy", "Date", "Reference", "Reason"])
            csv = df_exc.to_csv(index=False)
            st.download_button(
                "📝 Undantagsmall (CSV)",
                data=csv,
                file_name=f"undantag_{st.session_state.last_filename}_{datetime.now().strftime('%Y%m%d_%H%M')}.csv",
                mime="text/csv",
            )
            st.caption(f"{len(unique)} fel att granska")
        else:
            st.info("Inga fel att lägga till som undantag. 🎉")
