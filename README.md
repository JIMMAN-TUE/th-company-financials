# th-company-financials (`thfin`)

เครื่องมือดึง **รายได้ ต้นทุนขาย ค่าใช้จ่าย กำไรสุทธิ และสินทรัพย์รวม** ของนิติบุคคลไทยจากแหล่งข้อมูลทางการ
โดยกำหนดขอบเขต (scope) ได้จากไฟล์ YAML ไฟล์เดียว เพิ่มหรือลดได้ตลอด และรันต่อจากที่ค้างไว้ได้

> **สถานะ:** DataWarehouse+ ตอบ bot challenge จึงหยุดการดึงอัตโนมัติไว้ ใช้ export ด้วยมือ + import-files
> อ่าน [docs/HANDOVER.md](docs/HANDOVER.md) หัวข้อ "Findings" และวิธี export ด้วยมือด้านล่างก่อน

## ข้อจำกัดที่ต้องรู้ก่อน

- มีตัวเลขรายได้/ต้นทุนเฉพาะ **นิติบุคคลที่ยื่นงบการเงินกับ DBD** บุคคลธรรมดาและกิจการรายเล็กหลายแห่งไม่ยื่นงบ ดึงไม่ได้ด้วยวิธีใดเลย
- "ทั้งประเทศ" ไม่เหมาะกับการดึงทีละบริษัท ที่ความเร็วสุภาพ 0.5 ครั้ง/วินาที ทุก 1,000 บริษัทใช้ราว 33 นาที
  คำสั่ง `plan` จะบอกเวลาจริงของ scope ปัจจุบัน และเตือนเมื่อใหญ่เกินไป ถ้าต้องการครอบคลุมทั้งประเทศ ให้ติดต่อ DBD ขอข้อมูลแบบ bulk
- ต้องอ่านเงื่อนไขการใช้งานของเว็บต้นทางและกรอก [docs/LEGAL.md](docs/LEGAL.md) ก่อนรันจริง

## เริ่มใช้งาน

**Windows ไม่ต้องติดตั้ง Python:** ใช้แอป ZIP ที่รวม Python และ Chromium แล้ว
ดู [วิธีดาวน์โหลด เปิดใช้ และส่งต่อแอป](docs/WINDOWS_APP.md) เลือก scope แล้วกดรัน/นำเข้า/ดาวน์โหลด Excel
จากหน้าจอเดียวกัน ข้อมูลของแต่ละคนเก็บใน `%LOCALAPPDATA%\thfin`

ถ้ามี Python อยู่แล้ว เปิดแอปด้วย `thfin app` ส่วน `thfin ui` เดิมใช้ตั้ง scope อย่างเดียว

```bash
git clone <repo-url> && cd th-company-financials
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
make test                                  # เทสต์ข้อมูลสมมติ ไม่ติดต่อเว็บภายนอก
```

1. ดาวน์โหลดไฟล์ CSV ทะเบียนนิติบุคคลจาก DBD Open Data ไปไว้ที่ `data/registry/`
   แล้วรัน `python -m thfin registry-headers` เพื่อดูว่าตรวจจับชื่อคอลัมน์ได้ครบไหม
2. ตั้งอีเมลติดต่อจริงด้วย environment variable `THFIN_USER_AGENT="thfin-research/0.1 (+mailto:you@company.com)"`
   (หรือแก้ `runtime.user_agent` ใน `config/scope.yaml`) ถ้ายังเป็น `CHANGE-ME` โปรแกรมจะไม่ยอมรัน
3. ตั้ง scope แล้วรัน `python -m thfin plan` เพื่อดูจำนวนบริษัทและเวลาที่ต้องใช้
4. `python -m thfin probe <เลขนิติบุคคล 13 หลัก>` เพื่อเช็กว่าดึงตัวเลขของบริษัทหนึ่งได้ถูกต้อง
5. `python -m thfin run --limit 25` แล้ว `python -m thfin export` (ได้ CSV และ XLSX ใน `data/exports/`)

## ปรับ scope (เพิ่ม/ลด)

**วิธีง่ายสุด: หน้า UI** (รันในเครื่องที่มีไฟล์ registry)

```bash
python -m thfin ui          # เปิด http://127.0.0.1:8765
```

เลือกปี (ย้อนหลัง N ปี หรือเลือกปีเอง), จังหวัด, อุตสาหกรรม TSIC, ประเภท/สถานะ, ทุนจดทะเบียน, ช่วงรายได้ แล้วดูจำนวนบริษัทและเวลาที่ต้องใช้แบบสด
กด "บันทึก scope" เพื่อเขียนลง YAML (แก้เฉพาะส่วน `scope:` ไม่แตะส่วนอื่น) หรือตั้งชื่อไฟล์ใหม่ เช่น `presets/my_scope.yaml`
UI เปิดเฉพาะในเครื่อง (127.0.0.1) มี token ต่อรอบ และไม่ดึงข้อมูลจากอินเทอร์เน็ตเลย

**หรือแก้ไฟล์ตรงๆ:** `config/scope.yaml` หรือใช้ `--set` ชั่วคราวโดยไม่แก้ไฟล์:

```bash
python -m thfin plan --set scope.provinces=ชลบุรี,ระยอง --set 'scope.tsic_prefixes=["10","46"]'
python -m thfin plan --config config/presets/hotels_bangkok.yaml
```

| ค่าใน `scope` | ความหมาย | ว่าง = |
|---|---|---|
| `provinces` | จังหวัด (รู้จัก กรุงเทพ / กรุงเทพฯ / กทม.) | ทั้งประเทศ |
| `tsic_prefixes` | รหัสอุตสาหกรรม TSIC ขึ้นต้นด้วย เช่น `["10"]` อาหาร `["55"]` โรงแรม | ทุกอุตสาหกรรม |
| `juristic_types` | ประเภทนิติบุคคล (ค่าเริ่มต้น บจก. และ บมจ.) | ทุกประเภท |
| `statuses` | สถานะ (ค่าเริ่มต้น ดำเนินกิจการอยู่) | ทุกสถานะ |
| `name_contains` | ชื่อมีคำนี้ | ไม่กรอง |
| `capital_min/max` | ทุนจดทะเบียน (บาท) | ไม่กรอง |
| `years_back` | ย้อนหลังกี่ปี (ค่าเริ่มต้น 3 = ปีที่ปิดงบล่าสุด 3 ปี) | |
| `latest_fiscal_year` | ปีล่าสุดที่นับ (ว่าง = ปี พ.ศ. ปัจจุบัน − 1) ถ้าปีล่าสุดยังไม่ยื่นงบให้ใส่ปีก่อนหน้า | อัตโนมัติ |
| `fiscal_years` | ระบุปีเอง เช่น `[2566, 2567, 2568]` ถ้าใส่ จะใช้แทน `years_back` (ใส่ ค.ศ. ได้ แปลงให้เอง) | ใช้ `years_back` |
| `revenue_min/max` | ช่วงรายได้รวม กรองตอน export | ไม่กรอง |
| `include_ids` | เฉพาะเลขนิติบุคคลเหล่านี้ (ใส่เครื่องหมายคำพูดเสมอ) ตัวกรองอื่นถูกข้าม | ไม่ใช้ |
| `exclude_ids` | ตัดเลขเหล่านี้ออก | ไม่ตัด |
| `max_companies` | เพดานจำนวนต่อรอบ เพื่อกันรันใหญ่เกินโดยไม่ตั้งใจ (ค่าเริ่มต้น 200) | |

## ความปลอดภัยที่ฝังไว้

อนุญาตเฉพาะโฮสต์ใน `allowed_hosts` · เช็ก `robots.txt` · จำกัดความเร็ว + backoff · หยุดเองเมื่อผิดพลาดติดกัน 10 ครั้ง ·
cache กันดึงซ้ำ · รันเกิน 1,000 บริษัทต้องใส่ `--yes` · เก็บเฉพาะข้อมูลนิติบุคคล ไม่เก็บชื่อกรรมการหรือข้อมูลส่วนบุคคล

## ส่งต่อให้ทีม

- **Claude Code:** clone แล้วเปิดโฟลเดอร์ `CLAUDE.md` จะถูกโหลดอัตโนมัติ มี slash command
  `/scope` ปรับ scope · `/probe` ทดสอบหนึ่งบริษัทและแก้ parser · `/collect` รันเป็นชุด · `/qa` ตรวจคุณภาพข้อมูล · `/handover` อัปเดตเอกสารส่งต่อ
- **claude.ai:** แนบ repo ในเซสชัน หรือวาง `CLAUDE.md` กับ `docs/HANDOVER.md` ใน Project ให้ทีมเริ่มจากบริบทเดียวกัน

## โครงสร้าง

```
config/scope.yaml        ขอบเขตและค่าตั้งทั้งหมด     config/presets/   ตัวอย่าง scope
src/thfin/               config · scopeio · uiserver + ui/ · sources/registry · http · parsers · pipeline · store · cli
tests/                   เทสต์ใช้ข้อมูลสมมติ; browser tests ใช้ loopback
docs/                    DATA_SOURCES · LEGAL · HANDOVER · CLAUDE_CODE_SETUP
.claude/commands/        slash command สำหรับทีม
```

## Export ด้วยมือ (ใช้ได้ระหว่างที่ DataWarehouse+ บล็อกบอท)

อ่าน Findings ใน [docs/HANDOVER.md](docs/HANDOVER.md) ก่อน คำสั่งตัวช่วยด้านล่างทำงานกับไฟล์ในเครื่อง
ไม่เปิดเว็บอัตโนมัติ ส่วน Chromium backend อยู่ในแพ็กเกจเดียวกัน ดูวิธีติดตั้งด้านล่าง

```powershell
thfin plan --config config/scope.yaml
thfin queue --config config/scope.yaml
thfin watch-downloads --dir "$env:USERPROFILE\Downloads\thfin" --config config/scope.yaml
# หลัง export ครบ หยุด watcher แล้วนำเข้าไฟล์
thfin import-files --config config/scope.yaml --set financials.mode=files
thfin export --config config/scope.yaml
```

สร้างโฟลเดอร์ `Downloads\thfin` และตั้ง browser ให้ดาวน์โหลดลงโฟลเดอร์นี้เฉพาะงานนี้ก่อนเริ่ม
เปิด `manual-queue.html` ด้วย browser ด้วยตนเอง แล้วคลิกลิงก์ตามลำดับ (เลขทะเบียนเรียงจากน้อยไปมาก
และใช้ scope / max_companies เดิม) ลิงก์มาจาก `financials.url_template` ที่ยังไม่ยืนยัน URL จริง
ให้ตรวจ/ตั้งค่า template ด้วยตนเอง; โปรแกรมไม่ลอง URL หรือเพิ่ม allowed_hosts ให้

**เริ่ม watcher ก่อน export** แล้ว export ทีละบริษัท รวมปีที่ต้องการไว้ในไฟล์เดียว `.xlsx` หรือ `.html`
รอข้อความ `Moved` และ `Next company` ก่อนเริ่มบริษัทถัดไป ตัวเฝ้าจะตั้งชื่อ `<เลขทะเบียน 13 หลัก>.<นามสกุล>`
ใน `financials.files_dir` โดยจับคู่ตามลำดับคิวเท่านั้น ไม่สามารถยืนยันเนื้อหาว่าเป็นบริษัทนั้นจริง
อย่าดาวน์โหลดไฟล์งานอื่นระหว่างรอ หรือ export หลายไฟล์ต่อบริษัท

ไฟล์ที่มีอยู่ก่อนเริ่ม watcher จะไม่ถูกย้าย รวมถึงไฟล์ที่โหลดระหว่างหยุด watcher ให้เริ่ม watcher แล้ว
ดาวน์โหลดใหม่ รอให้ขนาด/เวลาแก้ไขไฟล์นิ่งอย่างน้อย 2 วินาที; `.crdownload`, `.part` และไฟล์ว่างจะยังไม่ถูกย้าย
หากต้องหยุดให้กด Ctrl+C แล้วใช้คำสั่งเดิม โปรแกรมจะอ่านความคืบหน้าจาก `manual-queue.html.json`
และพิมพ์บริษัทถัดไป (HTML ไม่แสดงความคืบหน้า) เมื่อครบคิว watcher จะจบเอง

ใช้ `queue --out <path.html>` กับ `watch-downloads --queue <path.html>` ถ้าต้องการแยกคิวใหม่
คิวเดิมจะไม่ถูกเขียนทับ และผูกกับ files_dir เดิม แม้แก้ scope ภายหลัง คิวที่บันทึกไว้จะไม่เปลี่ยนตาม
เก็บ HTML และ JSON ไว้ด้วยกันนอก Git; ชื่อ default ถูก gitignore ไว้แล้ว

ถ้าชื่อปลายทางซ้ำ ไฟล์ถูกล็อกโดย Excel หรือย้ายข้ามดิสก์ไม่สำเร็จ โปรแกรมหยุดโดยไม่เลื่อนคิว
และไม่เขียนทับไฟล์เดิม ตรวจทั้ง source/destination ก่อนเริ่มใหม่ กรณีคัดลอกค้างอาจเหลือไฟล์ทั้งสองที่:
เก็บ source ไว้ แล้วนำ destination ที่คัดลอกไม่ครบออกจากทาง ก่อนรัน watcher เดิมให้ลองบริษัทเดิมอีกครั้ง
หากเครื่องดับระหว่างย้ายและบันทึกสถานะ โปรแกรมจะกู้รายการที่ย้ายเสร็จแล้วเมื่อเริ่มใหม่
ล็อก `<ชื่อคิว>.html.lock` กันเปิด watcher ซ้อน; ถ้า process ถูก kill ให้ยืนยันว่าไม่มี watcher ทำงาน
แล้วค่อยลบไฟล์ lock ที่ค้าง (ไม่ลบ JSON ความคืบหน้า)

PoliteClient เคารพ `Crawl-delay` โดยเว้น `max(crawl_delay, 1 / requests_per_second)` วินาที
รวมการลองใหม่แต่ละครั้ง โดยใช้กลุ่ม User-agent ที่ตรงกับตัว client ตาม robots.txt

## Chromium backend (แพ็กเกจ thfin เดียวกัน)

สำหรับหน้าที่ JavaScript สร้างตาราง ใช้ตัวเลือก browser ของแพ็กเกจเดิม:

```powershell
pip install -e ".[browser]"
python -m playwright install chromium
thfin plan --config config/scope.yaml
thfin probe <เลขทะเบียน13หลัก> --set runtime.transport=chromium
thfin run --limit 25 --set runtime.transport=chromium
```

ใช้ `--set runtime.chromium_headless=false` เพื่อแสดงหน้าต่างบนเครื่องที่มี desktop
หรือ `--set runtime.chromium_executable=<path>` หากมี Chromium ติดตั้งเองแล้ว
โปรแกรมรอ selector `table` ก่อนส่ง DOM เข้า parser เดิม ปรับได้ด้วย `runtime.chromium_wait_selector`
ถ้ารอครบ timeout แล้วยังไม่มีตาราง จะส่ง DOM ให้ parser รายงานผลว่างตามจริง

Robots.txt ยังอ่านผ่าน HTTP ปกติก่อนเริ่ม browser; ถ้าอ่านไม่ได้/ถูกปฏิเสธจะไม่เริ่มดึงหน้า
หน้าเว็บ, script, stylesheet และ XHR ใช้ allowed_hosts, robots และ Crawl-delay เดียวกัน
รูปภาพ/ฟอนต์/สื่อ, service worker และ WebSocket ไม่ถูกโหลด
redirect หยุดก่อนตามไป ให้ตั้ง URL ปลายทางที่ตรวจสอบแล้วโดยตรง
cache ของ Chromium แยกจาก HTTP ปกติ จึงไม่ใช้ HTML เก่าที่ไม่มีตารางแทน DOM ที่ render แล้ว
ใช้ proxy ของ environment และตรวจ TLS ตามปกติ ไม่มีการเปลี่ยน UA หรือปิดการตรวจ certificate

เจ้าของอนุญาตใช้ Chromium เมื่อ 2026-10-29 และยกเลิกข้อห้าม headless แบบเหมารวมแล้ว
ผลทดสอบปัจจุบันยืนยันกับเว็บสมมติในเครื่องเท่านั้น ยังไม่ยืนยันว่าเข้า DBD ได้จริง
URL template และเงื่อนไขเว็บไซต์ยังต้องตรวจสอบตาม HANDOVER
