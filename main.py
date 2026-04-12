import os
import uuid
import shutil
from pathlib import Path
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from playwright.async_api import async_playwright


os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "/ms-playwright"
os.environ["PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD"] = "1"

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
REPORTS_DIR = BASE_DIR / "reports"

REPORTS_DIR.mkdir(exist_ok=True)

app = FastAPI()

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/reports", StaticFiles(directory=REPORTS_DIR), name="reports")

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


INSPECTION_SECTIONS = [
    {
        "key": "external",
        "name": "External areas",
        "checklist": [
            "Walls, roofline and visible external surfaces checked",
            "Paths, access points and entry conditions reviewed",
            "General presentation and visible defects noted"
        ]
    },
    {
        "key": "entry_living",
        "name": "Entry and living areas",
        "checklist": [
            "Walls, ceilings and flooring visually checked",
            "Doors, windows and basic fittings reviewed",
            "Signs of damage, wear or maintenance issues noted"
        ]
    },
    {
        "key": "kitchen",
        "name": "Kitchen",
        "checklist": [
            "Cabinetry, benchtops and sink area reviewed",
            "Appliances and fixtures visually checked",
            "Leaks, damage or cleanliness issues noted"
        ]
    },
    {
        "key": "bathroom",
        "name": "Bathroom",
        "checklist": [
            "Shower, vanity, toilet and fittings reviewed",
            "Tiling, seals and visible moisture issues checked",
            "Ventilation and general condition noted"
        ]
    },
    {
        "key": "bedrooms",
        "name": "Bedrooms",
        "checklist": [
            "Walls, flooring and wardrobes reviewed",
            "Windows, blinds and doors visually checked",
            "General condition and visible issues noted"
        ]
    },
    {
        "key": "laundry",
        "name": "Laundry",
        "checklist": [
            "Taps, trough and connections reviewed",
            "Drainage and visible water issues checked",
            "General condition noted"
        ]
    },
    {
        "key": "electrical",
        "name": "Electrical and lighting",
        "checklist": [
            "Visible switches, lights and power points reviewed",
            "Obvious damage or safety concerns noted",
            "Any inaccessible items recorded in notes"
        ]
    },
    {
        "key": "plumbing",
        "name": "Plumbing",
        "checklist": [
            "Visible taps, fixtures and drainage points reviewed",
            "Leaks, pressure concerns or damage noted",
            "Water related observations recorded"
        ]
    },
    {
        "key": "safety",
        "name": "Safety items",
        "checklist": [
            "Smoke alarms and visible safety items reviewed",
            "Trip hazards or immediate safety concerns noted",
            "Any urgent follow up recorded"
        ]
    },
    {
        "key": "general_defects",
        "name": "General defects and recommendations",
        "checklist": [
            "Outstanding defects captured",
            "Recommended repairs or follow up noted",
            "Photo evidence attached where relevant"
        ]
    }
]


@app.get("/", response_class=HTMLResponse)
async def form(request: Request):
    return templates.TemplateResponse(
        "form.html",
        {"request": request, "sections": INSPECTION_SECTIONS}
    )


@app.post("/generate")
async def generate_report(request: Request):
    form = await request.form()

    report_id = f"INSP-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}"
    report_dir = REPORTS_DIR / report_id
    report_dir.mkdir(exist_ok=True)

    sections = []
    table_rows = []
    all_photos = []
    attention_required_count = 0

    for section_def in INSPECTION_SECTIONS:
        key = section_def["key"]

        status = form.get(f"{key}_status") or "Good"
        condition = form.get(f"{key}_condition") or ""
        note = form.get(f"{key}_notes") or ""

        status_class = "ok"
        summary = "No major issue noted"

        if status == "Needs attention":
            status_class = "bad"
            summary = "Issue identified and follow up recommended"
            attention_required_count += 1
        elif status == "Monitor":
            status_class = "monitor"
            summary = "Condition should be monitored"
        elif status == "Not inspected":
            status_class = "na"
            summary = "Area was not inspected"

        photos = []
        uploads = form.getlist(f"{key}_photos")

        for upload in uploads:
            if upload.filename:
                safe_name = f"{uuid.uuid4().hex}_{upload.filename}"
                photo_path = report_dir / safe_name

                with open(photo_path, "wb") as buffer:
                    shutil.copyfileobj(upload.file, buffer)

                photo_url = photo_path.resolve().as_uri()
                photos.append(photo_url)
                all_photos.append(photo_url)

        sections.append({
            "name": section_def["name"],
            "checklist": section_def["checklist"],
            "status": status,
            "status_class": status_class,
            "condition": condition,
            "note": note,
            "photos": photos,
        })

        table_rows.append({
            "name": section_def["name"],
            "status": status,
            "status_class": f"status-{status_class}",
            "summary": summary,
        })

    logo_uri = (STATIC_DIR / "class-a-fix-logo.jpg").resolve().as_uri()

    context = {
        "property_address": form.get("property_address") or "",
        "agency": form.get("agency") or "",
        "inspector_name": form.get("inspector_name") or "",
        "inspection_date": form.get("inspection_date") or "",
        "property_manager": form.get("property_manager") or "",
        "tenant_status": form.get("tenant_status") or "",
        "overall_summary": form.get("overall_summary") or "",
        "generated_date": datetime.now().strftime("%d %b %Y"),
        "reference": report_id,
        "overall_status": "Pass" if attention_required_count == 0 else "Action required",
        "areas_checked": len(INSPECTION_SECTIONS),
        "attention_required_count": attention_required_count,
        "sections": sections,
        "table_rows": table_rows,
        "all_photos": all_photos,
        "logo_uri": logo_uri,
    }

    html = templates.get_template("report.html").render(context)

    html_path = report_dir / "report.html"
    html_path.write_text(html, encoding="utf-8")

    pdf_path = report_dir / f"{report_id}.pdf"

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--single-process",
            ],
        )

        page = await browser.new_page()
        await page.goto(html_path.resolve().as_uri(), wait_until="networkidle")
        await page.pdf(path=str(pdf_path), format="A4", print_background=True)
        await browser.close()

    return FileResponse(pdf_path, media_type="application/pdf", filename=pdf_path.name)
