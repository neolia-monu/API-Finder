      /*
  Frontend SPA logic:
  - GET /  -> returns { data: [...] } (list of APIs)
  - POST /enrich -> existing enrichment endpoint
*/

      // endpoints (use full local URLs)
      const ALL_URL = "http://127.0.0.1:3000/ ";
      const ENRICH_URL = "http://127.0.0.1:3000/enrich";

      // Tabs
      const tabHome = document.getElementById("tabHome");
      const tabRec = document.getElementById("tabRec");
      const viewHome = document.getElementById("viewHome");
      const viewRec = document.getElementById("viewRec");

      tabHome.addEventListener("click", () => {
        showView("home");
      });
      tabRec.addEventListener("click", () => {
        showView("rec");
      });

      function showView(which) {
        if (which === "home") {
          viewHome.style.display = "";
          viewRec.style.display = "none";
          tabHome.classList.add("active");
          tabRec.classList.remove("active");
        } else {
          viewHome.style.display = "none";
          viewRec.style.display = "";
          tabHome.classList.remove("active");
          tabRec.classList.add("active");
        }
      }

      // load all APIs and render
      const cardsEl = document.getElementById("cards");
      const refreshBtn = document.getElementById("refreshBtn");

      function makeLoadingNode(text = "Loading APIs…") {
        const container = document.createElement("div");
        container.className = "loading-center"; // centered style
        const spinner = document.createElement("span");
        spinner.className = "spinner-inline";
        const label = document.createElement("span");
        label.textContent = text;
        container.appendChild(spinner);
        container.appendChild(label);
        return container;
      }

      async function fetchAll() {
        cardsEl.innerHTML = "";
        cardsEl.appendChild(makeLoadingNode("Loading APIs…"));
        try {
          const r = await fetch(ALL_URL, { cache: "no-store" });
          if (!r.ok) throw new Error(await r.text());
          const payload = await r.json();
          const data = payload.data || [];
          renderCards(data);
        } catch (err) {
          cardsEl.innerHTML = "";
          const n = document.createElement("div");
          n.className = "muted";
          n.innerHTML = `Failed to load APIs: ${escapeHtml(err.message)}`;
          cardsEl.appendChild(n);
        }
      }

      // Convert ISO date → human readable (e.g., "June 15 2024 · 12:00 PM")
      function formatDate(iso) {
        if (!iso) return "—";
        const d = new Date(iso);
        if (isNaN(d)) return "—";
        const options = {
          year: "numeric",
          month: "long",
          day: "numeric",
          hour: "2-digit",
          minute: "2-digit",
        };
        /*return d.toLocaleString('en-US', options).replace(',', ' ·');*/
        return d.toLocaleString("en-US", { ...options, timeZone: "UTC" });
      }

      function renderCards(list) {
        if (!Array.isArray(list) || list.length === 0) {
          cardsEl.innerHTML = '<div class="muted">No APIs found.</div>';
          return;
        }
        cardsEl.innerHTML = "";

        list.forEach((api) => {
          const card = document.createElement("div");
          card.className = "card";

          const name = api.api_name || "—";
          const summary = api.summary || api.description || "";
          const owner = api.owner || "—";
          const rating = api.rating !== undefined ? api.rating : "—";
          const tags = Array.isArray(api.tags) ? api.tags : [];
          const created = formatDate(api.created_at || api.created);
          const updated = formatDate(api.updated_at || api.updated);
          const swaggerHref = api.swagger || api.documentation || "#";
          const exampleRequest = api.example_request
            ? JSON.stringify(api.example_request, null, 2)
            : "";
          const predictedResponse = api.predicted_response_shape
            ? JSON.stringify(api.predicted_response_shape, null, 2)
            : "";

          const usageId = "usage_" + Math.random().toString(36).slice(2, 9);

          card.innerHTML = `
      <h3>API Name: <a class="link" href="${escapeHtml(
        swaggerHref
      )}" target="_blank" rel="noreferrer">${escapeHtml(name)}</a></h3>

      <div class="meta">
        <div class="small">Owner: <strong>${escapeHtml(owner)}</strong></div>
        <div class="small">Updated: ${escapeHtml(updated)}</div>
        <div class="small">Created: ${escapeHtml(created)}</div>
        <div style="margin-left:auto;">Rating: <span class="badge">${escapeHtml(
          rating
        )}</span></div>
      </div>

      <p class="summary">Summary: ${escapeHtml(summary)}</p>

      <div>Tags: ${tags
        .map((t) => `<span class="tag">${escapeHtml(t)}</span>`)
        .join(" ")}</div>

      <div style="margin-top:8px;">
        <button class="usage-toggle" data-target="${usageId}">Show usage / Swagger</button>
        <div id="${usageId}" class="usage-panel" aria-hidden="true"></div>
      </div>
    `;

          cardsEl.appendChild(card);

          // after injecting into DOM, attach data and listener to the toggle button
          const btn = card.querySelector(".usage-toggle");
          const panel = card.querySelector(".usage-panel");

          // store usage data on the button (safe, not in DOM attributes)
          btn._usage = {
            documentation: api.documentation || "",
            swagger: api.swagger || "",
            exampleRequest,
            predictedResponse,
          };

          // Toggle handler: populate panel only on first open
          btn.addEventListener("click", () => {
            const isOpen = panel.style.display === "block";
            if (isOpen) {
              panel.style.display = "none";
              panel.setAttribute("aria-hidden", "true");
              btn.textContent = "Show usage / Swagger";
              return;
            }

            // If panel empty, build content from stored data
            if (!panel.innerHTML.trim()) {
              const parts = [];
              const u = btn._usage;

              if (u.documentation) {
                parts.push(
                  `<div><strong>Documentation:</strong> <a href="${escapeHtml(
                    u.documentation
                  )}" target="_blank">${escapeHtml(u.documentation)}</a></div>`
                );
              }
              if (u.swagger) {
                parts.push(
                  `<div><strong>Swagger:</strong> <a href="${escapeHtml(
                    u.swagger
                  )}" target="_blank">${escapeHtml(u.swagger)}</a></div>`
                );
              }
              if (u.exampleRequest) {
                parts.push(
                  `<div style="margin-top:8px;"><strong>Example request:</strong><pre>${escapeHtml(
                    u.exampleRequest
                  )}</pre></div>`
                );
              }
              if (u.predictedResponse) {
                parts.push(
                  `<div style="margin-top:8px;"><strong>Predicted response shape:</strong><pre>${escapeHtml(
                    u.predictedResponse
                  )}</pre></div>`
                );
              }

              // If no parts, show message now (only visible after click)
              if (parts.length === 0) {
                parts.push(
                  '<div class="muted">No usage details available.</div>'
                );
              }

              panel.innerHTML = parts.join("<br/>");
            }

            panel.style.display = "block";
            panel.setAttribute("aria-hidden", "false");
            btn.textContent = "Hide usage / Swagger";
          });
        });
      }

      // small escaping to avoid injection when rendering names/links
      function escapeHtml(s) {
        if (s === null || s === undefined) return "";
        return String(s)
          .replace(/&/g, "&amp;")
          .replace(/</g, "&lt;")
          .replace(/>/g, "&gt;")
          .replace(/"/g, "&quot;")
          .replace(/'/g, "&#39;");
      }

      // refresh button
      refreshBtn.addEventListener("click", fetchAll);

      // initial load
      fetchAll();

      // ---------------- Recommendations view ----------------
      const runRecBtn = document.getElementById("runRec");
      const recQuery = document.getElementById("recQuery");
      const recStatus = document.getElementById("recStatus");
      const recResults = document.getElementById("recResults");
      const recSearch = document.getElementById("search");

      if (runRecBtn) {
        runRecBtn.addEventListener("click", runRec);
      }
      if (recQuery) {
        recQuery.addEventListener("keydown", (e) => {
          if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            runRec();
          }
        });
      }

      // helper to show spinner + text
      function showRecLoading(text = "Searching…") {
        recSearch.innerHTML = "";
        recSearch.className = "loading-center";

        const spinner = document.createElement("span");
        spinner.className = "spinner-inline";
        const label = document.createElement("span");
        label.textContent = text;

        recSearch.appendChild(spinner);
        recSearch.appendChild(label);

        // Hide results during search
        recResults.innerHTML = "";
        recResults.style.display = "none";
      }

      function hideRecLoading() {
        recSearch.className = "muted";
        recSearch.innerHTML = "";
        recResults.style.display = "";
        recStatus.className = "muted";
        recStatus.innerHTML = "";
      }

      async function runRec() {
        const q = recQuery.value.trim();
        if (!q) {
          alert("Please enter a description or query");
          recQuery.focus();
          return;
        }
        showRecLoading("Searching…");
        recResults.innerHTML = "";
        try {
          const r = await fetch(ENRICH_URL, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ query: q }),
          });
          const text = await r.text();
          if (!r.ok) throw new Error(text || r.statusText);
          const payload = JSON.parse(text);
          const results = payload.results || [];
          renderRecResults(results);
          hideRecLoading();
          recStatus.textContent = `Found ${results.length} result(s)`;
          // switch to recommendations view (in case user called from URL)
          showView("rec");
        } catch (err) {
          hideRecLoading();
          recStatus.textContent = `Error: ${err.message}`;
        }
      }

      function renderRecResults(results) {
        if (!Array.isArray(results) || results.length === 0) {
          recResults.innerHTML =
            '<div class="muted">No recommendations found.</div>';
          return;
        }
        recResults.innerHTML = "";
        results.forEach((r) => {
          const rc = document.createElement("div");
          rc.className = "result-card";
          const left = document.createElement("div");
          left.className = "result-left";
          const title = `<div style="display:flex; justify-content:space-between; align-items:center; gap:12px;">
                     <div><a class="link" href="${escapeHtml(
                       r.api_name
                     )}" target="_blank">API name: ${escapeHtml(
            r.api_name
          )}</a></div>
                     <div class="muted small">Match Type: ${escapeHtml(
                       r.match_type || ""
                     )}</div>
                   </div>`;
          const summary = `<div class="small" style="margin-top:6px; line-height:1.5;">Summary: ${escapeHtml(
            r.summary || ""
          )}</div>`;
          const tags =
            Array.isArray(r.matched_tags) && r.matched_tags.length
              ? `<div style="margin-top:8px">${r.matched_tags
                  .map((t) => `<span class="tag">${escapeHtml(t)}</span>`)
                  .join(" ")}</div>`
              : "";
          const why = r.why
            ? `<div class="muted small" style="margin-top:8px">Why: ${escapeHtml(
                r.why
              )}</div>`
            : "";
          left.innerHTML = title + summary + tags + why;

          const scoreBox = document.createElement("div");
          scoreBox.className = "result-score";
          scoreBox.textContent =
            typeof r.score === "number" ? r.score.toFixed(2) : r.score || "—";

          rc.appendChild(left);
          rc.appendChild(scoreBox);
          recResults.appendChild(rc);
        });
      }

      // Optional: Show recommendations view on load if a query param ?rec=1 is present
      if (location.search.includes("rec=1")) {
        showView("rec");
      }