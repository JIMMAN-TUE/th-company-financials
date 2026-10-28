/* Uses the existing scope widget's collect(), preview(), api(), el() and TOKEN. */
document.body.classList.add("app-mode");
document.title = "thfin · งบการเงินตามขอบเขต";
document.querySelector("header h1").textContent = "thfin · งบการเงินตามขอบเขตที่เลือก";
const statusLabels = {idle:"พร้อมเริ่มงาน", running:"กำลังทำงาน", stopping:"กำลังหยุดหลังจบรายการปัจจุบัน", cancelled:"หยุดแล้ว · เริ่มต่อได้", completed:"งานเสร็จแล้ว", failed:"งานหยุด — ดูรายละเอียดด้านล่าง"};
const actionLabels = {collect:"ดึงข้อมูลงบ", queue:"สร้างคิว export", watch:"เฝ้าดาวน์โหลด", import:"นำเข้างบ"};
let lastJobState = "", appBusy = false;
document.addEventListener("scopechange", () => {
  $("resultCount").textContent = "";
  $("resultWrap").replaceChildren(el("p", {className:"small", textContent:"ขอบเขตเปลี่ยนแล้ว กดดูผลลัพธ์เพื่ออัปเดตตาราง"}));
});
function message(id, text, kind="err"){ $(id).replaceChildren(el("div", {className:"msg " + kind, textContent:text})); }
function showError(e){ message("actionError", e.message + (e.details?.length ? " · " + e.details.join(" · ") : "")); }
async function startJob(action){
  $("actionError").replaceChildren();
  try{
    await preview();
    const job = await api("/api/job/start", {action, scope:collect(), limit:Number($("batchLimit").value), refetch:$("refetchJob").checked, directory:$("downloadDir").value.trim()});
    renderJob(job);
  }catch(e){ showError(e); }
}
function renderJob(job){
  appBusy = ["running","stopping"].includes(job.status);
  ["collectJob","queueJob","watchJob","importJob","registryUpload","statementUpload","saveSource"].forEach(id => $(id).disabled = appBusy);
  ["openQueue","exportXlsx","exportCsv"].forEach(id => $(id).disabled = appBusy && job.action === "watch");
  $("stopJob").disabled = !appBusy || job.status === "stopping";
  $("jobStatus").className = "msg " + (job.status === "failed" ? "err" : job.status === "completed" ? "ok" : "warn");
  $("jobStatus").textContent = (actionLabels[job.action] ? actionLabels[job.action] + " · " : "") + (statusLabels[job.status] || job.status);
  $("jobLog").hidden = !job.logs?.length;
  $("jobLog").textContent = (job.logs || []).join("\n");
  if(job.plan){
    const provinces = job.scope.provinces.length ? job.scope.provinces.join(", ") : "ทุกจังหวัด";
    const tsic = job.scope.tsic_prefixes.length ? "TSIC " + job.scope.tsic_prefixes.join(", ") : "ทุกอุตสาหกรรม";
    $("jobScope").textContent = `ขอบเขตของงานนี้: ${provinces} · ${tsic} · ตรงเงื่อนไข ${fmt(job.plan.matched)} · เลือก ${fmt(job.plan.selected)} · ประมาณ ${fmtTime(job.plan.est_hours)}`;
  }
  if(job.error) message("actionError", job.error);
  const state = job.id + ":" + job.status;
  if(state !== lastJobState && !appBusy && job.id){ preview(); refreshResults(); }
  lastJobState = state;
}
async function pollJob(){
  try{ renderJob(await api("/api/job")); }catch(e){ $("jobStatus").textContent = "แอปหยุดทำงานหรือเชื่อมต่อไม่ได้ กรุณาเปิด thfin.exe อีกครั้ง"; }
  setTimeout(pollJob, 1200);
}
async function loadSource(){
  const s = await api("/api/settings");
  $("sourceUrl").value = s.url_template; $("sourceAgent").value = s.user_agent;
  $("sourceTransport").value = s.transport; $("sourceMultiplier").value = s.unit_multiplier;
  $("workspacePath").textContent = "โฟลเดอร์ข้อมูลของคุณ: " + s.workspace;
}
async function uploadFiles(input, kind){
  try{
    for(const file of input.files){
      if(file.size > 24 * 1024 * 1024) throw new Error(file.name + " เกิน 24 MB");
      message("uploadMessage", "กำลังนำเข้า " + file.name, "warn");
      const content = await new Promise((resolve,reject) => {
        const reader = new FileReader(); reader.onload = () => resolve(reader.result.split(",")[1]); reader.onerror = reject; reader.readAsDataURL(file);
      });
      await api("/api/upload", {kind, name:file.name, content});
    }
    message("uploadMessage", "นำเข้าไฟล์แล้ว", "ok");
    OPT = await api("/api/options");
    $("cfgpath").textContent = "ทะเบียน " + fmt(OPT.total) + " บริษัท · ทำงานในเครื่องของคุณ";
    drawProvinces(); drawTsic(); await preview();
  }catch(e){ message("uploadMessage", e.message); }
  input.value = "";
}
async function refreshResults(){
  try{
    const r = await api("/api/results", {scope:collect()});
    $("resultCount").textContent = "· " + fmt(r.total) + " แถว";
    if(!r.rows.length){ $("resultWrap").replaceChildren(el("p", {className:"small", textContent:"ยังไม่มีงบตรงกับขอบเขตนี้ กรุณารวบรวมหรือนำเข้าไฟล์งบก่อน"})); return; }
    const cols = [["juristic_id","เลขทะเบียน"],["name_th","บริษัท"],["fiscal_year","ปีงบ"],["revenue_total","รายได้รวม"],["cost_of_sales","ต้นทุนขาย"],["net_profit","กำไรสุทธิ"]];
    const table = el("table", {}, el("thead", {}, el("tr", {}, ...cols.map(c => el("th", {textContent:c[1]})))), el("tbody", {}, ...r.rows.map(row => el("tr", {}, ...cols.map(([key]) => el("td", {textContent:row[key] == null ? "—" : typeof row[key] === "number" && key !== "fiscal_year" ? fmt(row[key]) : String(row[key])}))))));
    $("resultWrap").replaceChildren(table);
  }catch(e){ showError(e); }
}
async function download(path, data, filename){
  try{
    const response = await fetch(path, {method:"POST", headers:{"X-Token":TOKEN,"Content-Type":"application/json"}, body:JSON.stringify(data)});
    if(!response.ok){ const err = await response.json(); throw new Error(err.error || "ดาวน์โหลดไม่สำเร็จ"); }
    const url = URL.createObjectURL(await response.blob());
    const a = el("a", {href:url, download:filename}); document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(url), 60000);
  }catch(e){ showError(e); }
}
async function initApp(){
  // init() above owns widget loading. Wait until its saved scope has been applied before reading it.
  if(!OPT || !$("cfgpath").textContent){ setTimeout(initApp,100); return; }
  try{ await loadSource(); }catch(e){ showError(e); }
  $("collectJob").onclick = () => startJob("collect"); $("queueJob").onclick = () => startJob("queue");
  $("watchJob").onclick = () => startJob("watch"); $("importJob").onclick = () => startJob("import");
  $("stopJob").onclick = async () => { try{ renderJob(await api("/api/job/stop", {})); }catch(e){ showError(e); } };
  $("registryUpload").onchange = () => uploadFiles($("registryUpload"), "registry");
  $("statementUpload").onchange = () => uploadFiles($("statementUpload"), "statement");
  $("openQueue").onclick = () => download("/api/queue-html", {}, "manual-queue.html");
  $("refreshResults").onclick = refreshResults;
  $("exportXlsx").onclick = () => download("/api/export", {scope:collect(), format:"xlsx"}, "financials.xlsx");
  $("exportCsv").onclick = () => download("/api/export", {scope:collect(), format:"csv"}, "financials.csv");
  $("saveSource").onclick = async () => {
    try{
      await api("/api/settings", {url_template:$("sourceUrl").value.trim(), user_agent:$("sourceAgent").value.trim(), transport:$("sourceTransport").value, unit_multiplier:Number($("sourceMultiplier").value)});
      message("sourceMessage", "บันทึกแหล่งข้อมูลแล้ว", "ok");
    }catch(e){ message("sourceMessage", e.message); }
  };
  pollJob();
}
initApp();
