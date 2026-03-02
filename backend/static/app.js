async function api(path, method = "GET", token = "", body = null) {
  const headers = {
    Authorization: "Bearer " + token,
  };
  if (body) headers["Content-Type"] = "application/json";

  const r = await fetch(path, {
    method,
    headers,
    body: body ? JSON.stringify(body) : null,
  });
  const txt = await r.text();
  let data = null;
  try {
    data = JSON.parse(txt);
  } catch {
    data = txt;
  }
  if (!r.ok)
    throw new Error(
      typeof data === "string" ? data : JSON.stringify(data, null, 2),
    );
  return data;
}

function driveFileLink(fileId) {
  if (!fileId) return "";
  return `https://drive.google.com/file/d/${fileId}/view`;
}

function renderJobs(list) {
  const el = document.getElementById("jobs");
  el.innerHTML = "";

  for (const j of list) {
    const meta = j.meta_json || {};
    const drive = j.drive_json || {};
    const url = drive.final_mp4_file_id
      ? driveFileLink(drive.final_mp4_file_id)
      : "";

    const div = document.createElement("div");
    div.className = "job";
    div.innerHTML = `
      <div><span class="badge">${j.status}</span> <b>${j.topic}</b></div>
      <div class="muted">id: ${j.id}</div>
      <div class="muted">lang: ${j.lang} duration: ${j.duration_sec} voice: ${j.voice_model}</div>
      <div class="muted">${j.error || ""}</div>
      <div class="muted">Drive MP4: ${url ? `<a href="${url}" target="_blank">open</a>` : "not uploaded yet"}</div>
    `;
    el.appendChild(div);
  }
}

document.getElementById("create").addEventListener("click", async () => {
  const token = document.getElementById("token").value.trim();
  const topic = document.getElementById("topic").value.trim();
  const keywords = document
    .getElementById("keywords")
    .value.split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  const duration_sec =
    parseInt(document.getElementById("duration").value, 10) || 60;
  const lang = document.getElementById("lang").value.trim() || "en";
  const voice_model =
    document.getElementById("voice").value.trim() || "en_US-lessac-medium";

  const out = document.getElementById("createResult");
  out.textContent = "Creating job...";

  try {
    const res = await api("/api/jobs", "POST", token, {
      topic,
      keywords,
      duration_sec,
      lang,
      voice_model,
    });
    out.textContent = JSON.stringify(res, null, 2);
  } catch (e) {
    out.textContent = String(e.message || e);
  }
});

document.getElementById("refresh").addEventListener("click", async () => {
  const token = document.getElementById("token").value.trim();
  const out = document.getElementById("createResult");
  out.textContent = "Refreshing...";

  try {
    const list = await api("/api/jobs?limit=50", "GET", token);
    renderJobs(list);
    out.textContent = "Done.";
  } catch (e) {
    out.textContent = String(e.message || e);
  }
});
