const discover$ = id => document.getElementById(id);
const esc = val => String(val ?? "").replace(/[&<>'"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[c]));

const selectedInterests = new Set();
const selectedDestinations = new Map();
let currentCandidateCards = [];
let discoveryController = null;

function localDateString(date) {
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, "0");
  const day = String(date.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function getDiscoveryDates() {
  const startDate = discover$("discoverStartDate").value;
  const endDate = discover$("discoverEndDate").value;
  if (!startDate || !endDate) return null;

  const start = new Date(`${startDate}T12:00:00`);
  const end = new Date(`${endDate}T12:00:00`);
  const days = Math.floor((end - start) / 86400000) + 1;
  return { startDate, endDate, days };
}

function syncDiscoveryDateLimits() {
  const startInput = discover$("discoverStartDate");
  const endInput = discover$("discoverEndDate");
  if (!startInput || !endInput) return;
  const today = localDateString(new Date());
  if (!startInput.value) {
    const defaultStart = new Date();
    defaultStart.setDate(defaultStart.getDate() + 7);
    startInput.value = localDateString(defaultStart);
  }
  startInput.min = today;
  endInput.min = startInput.value;
  if (!endInput.value || endInput.value < startInput.value) {
    const defaultEnd = new Date(`${startInput.value}T12:00:00`);
    defaultEnd.setDate(defaultEnd.getDate() + 4);
    endInput.value = localDateString(defaultEnd);
  }

  const selectedStart = new Date(`${startInput.value}T12:00:00`);
  const monthSelect = discover$("discoverMonth");
  const yearSelect = discover$("discoverYear");
  if (monthSelect) {
    monthSelect.value = selectedStart.toLocaleString("en-US", { month: "long" });
  }
  if (yearSelect && Array.from(yearSelect.options).some(option => option.value === String(selectedStart.getFullYear()))) {
    yearSelect.value = String(selectedStart.getFullYear());
  }
}

syncDiscoveryDateLimits();
discover$("discoverStartDate")?.addEventListener("change", syncDiscoveryDateLimits);

// Interest toggle buttons
discover$("discoverInterests")?.addEventListener("click", event => {
  if (event.target.tagName !== "BUTTON") return;
  const tag = event.target.textContent.trim();
  if (selectedInterests.has(tag)) {
    selectedInterests.delete(tag);
    event.target.classList.remove("selected");
  } else {
    selectedInterests.add(tag);
    event.target.classList.add("selected");
  }
});

function updateSelectionDrawer() {
  const drawer = discover$("selectionDrawer");
  const countBadge = discover$("selectedCount");
  const chipsContainer = discover$("selectedChips");

  const count = selectedDestinations.size;
  countBadge.textContent = count;

  if (count === 0) {
    drawer.classList.add("hidden");
    chipsContainer.innerHTML = "";
    return;
  }

  drawer.classList.remove("hidden");
  chipsContainer.innerHTML = Array.from(selectedDestinations.values()).map(dest => `
    <span class="selected-chip">
      ${esc(dest.name)}
      <button type="button" class="remove-chip-btn" data-remove-dest="${esc(dest.name)}" title="Remove">×</button>
    </span>
  `).join("");

  // Attach chip remove listeners
  chipsContainer.querySelectorAll(".remove-chip-btn").forEach(btn => {
    btn.onclick = () => {
      const name = btn.dataset.removeDest;
      selectedDestinations.delete(name);
      renderCards();
      updateSelectionDrawer();
    };
  });
}

function renderCards() {
  const grid = discover$("candidateGrid");
  if (!currentCandidateCards.length) {
    grid.innerHTML = `<div class="error">No destinations found. Try broadening your location or selecting another month.</div>`;
    return;
  }

  grid.innerHTML = currentCandidateCards.map((card, index) => {
    const isSelected = selectedDestinations.has(card.name);
    return `
      <article class="data-card clean-destination-card ${isSelected ? 'is-selected' : ''}">
        <div class="card-header-row">
          <div class="dest-title-group">
            <h3>🌴 ${esc(card.name)}</h3>
            <span class="dest-region">${esc(card.region)}</span>
          </div>
          <div class="badge-group">
            <span class="match-score-badge">${esc(card.match_score)}% Match</span>
            <span class="status-badge ${card.status === 'VERIFIED' ? 'VERIFIED' : 'AI_RECOMMENDATION'}">${esc(card.status)}</span>
          </div>
        </div>

        <div class="card-section">
          <span class="card-label">Why visit this month?</span>
          <p class="card-text">${esc(card.why_visit)}</p>
        </div>

        ${card.special_this_month ? `
        <div class="card-section special-highlight-section">
          <span class="card-label special-label">✨ Special this month:</span>
          <p class="card-text highlight-text">
            ${esc(card.special_this_month)}
            ${card.special_is_verified ? '<span class="verified-tag">✓ Verified for Year</span>' : '<span class="estimated-tag">⚠️ Check Year Dates</span>'}
          </p>
        </div>
        ` : ''}

        <div class="card-section">
          <span class="card-label">Best for:</span>
          <div class="tag-pills">
            ${(card.best_for || []).map(tag => `<span class="tag-pill">${esc(tag)}</span>`).join('')}
          </div>
        </div>

        <div class="card-section travel-info">
          <span class="card-label">Travel connectivity:</span>
          <span class="travel-text">🚗 ${esc(card.travel_time)}</span>
        </div>

        <div class="card-action-row">
          <button class="toggle-trip-btn ${isSelected ? 'added' : ''}" type="button" data-dest-index="${index}">
            ${isSelected ? '✓ Added' : '+ Add to Trip'}
          </button>
          <button class="direct-plan-btn primary-subtle" type="button" data-dest-index="${index}">
            Plan Itinerary →
          </button>
          <button class="view-details-btn" type="button" data-dest-index="${index}">
            Details
          </button>
        </div>
      </article>
    `;
  }).join("");

  // Attach card button listeners
  grid.querySelectorAll(".toggle-trip-btn").forEach(btn => {
    btn.onclick = () => {
      const index = Number(btn.dataset.destIndex);
      const card = currentCandidateCards[index];
      if (selectedDestinations.has(card.name)) {
        selectedDestinations.delete(card.name);
      } else {
        selectedDestinations.set(card.name, card);
      }
      renderCards();
      updateSelectionDrawer();
    };
  });

  grid.querySelectorAll(".direct-plan-btn").forEach(btn => {
    btn.onclick = () => {
      const index = Number(btn.dataset.destIndex);
      const card = currentCandidateCards[index];
      selectedDestinations.set(card.name, card);
      goToTripPlanner([card]);
    };
  });

  grid.querySelectorAll(".view-details-btn").forEach(btn => {
    btn.onclick = () => {
      const index = Number(btn.dataset.destIndex);
      const card = currentCandidateCards[index];
      openDetailsModal(card);
    };
  });
}

function goToTripPlanner(cardsToPlan) {
  const origin = discover$("discoverOrigin").value.trim();
  const month = discover$("discoverMonth").value;
  const year = discover$("discoverYear").value || "2026";
  const dates = getDiscoveryDates();
  const days = dates?.days;
  if (!dates || !Number.isInteger(days) || days < 1 || days > 30) {
    alert("Please select a valid start and end date within 30 days.");
    return;
  }
  const pace = discover$("discoverPace")?.value || "balanced";
  const budget = discover$("discoverBudget")?.value === "flexible" ? "" : (discover$("discoverBudget")?.value || "");
  const destList = cardsToPlan.map(c => c.name).join(", ");
  
  // Save discovery metadata to sessionStorage
  try {
    sessionStorage.setItem("tripmate_discovery_cards", JSON.stringify(cardsToPlan));
    sessionStorage.setItem("tripmate_discovery_origin", origin);
    sessionStorage.setItem("tripmate_discovery_month", month);
    sessionStorage.setItem("tripmate_discovery_year", year);
    sessionStorage.setItem("tripmate_discovery_start_date", dates.startDate);
    sessionStorage.setItem("tripmate_discovery_end_date", dates.endDate);
  } catch (e) {}

  const params = new URLSearchParams({
    destination: destList,
    origin: origin,
    month: month,
    year: year,
    start_date: dates.startDate,
    end_date: dates.endDate,
    days: String(days),
    pace: pace,
    budget: budget,
    from_discovery: "true",
    autostart: "true"
  });

  window.location.href = `/?${params.toString()}`;
}

function openDetailsModal(card) {
  const modal = discover$("detailsModal");
  discover$("modalTitle").textContent = `${card.name} (${card.region})`;
  
  discover$("modalBody").innerHTML = `
    <div class="modal-detail-row">
      <strong>Match Score:</strong> <span class="match-score-badge">${card.match_score}% Match</span>
    </div>
    <div class="modal-detail-row">
      <strong>Why Visit This Month:</strong>
      <p>${esc(card.why_visit)}</p>
    </div>
    ${card.special_this_month ? `
      <div class="modal-detail-row">
        <strong>Seasonal Event:</strong>
        <p>${esc(card.special_this_month)} (${card.special_is_verified ? 'Verified' : 'Verify exact dates for current year'})</p>
      </div>
    ` : ''}
    <div class="modal-detail-row">
      <strong>Top Attractions & Highlights:</strong>
      <ul class="modal-attractions-list">
        ${(card.key_attractions || []).map(item => `<li>• ${esc(item)}</li>`).join('')}
      </ul>
    </div>
    <div class="modal-detail-row">
      <strong>Climate / Weather Overview:</strong>
      <p>${esc(card.weather_summary || "Seasonal weather check recommended before departure.")}</p>
    </div>
    <div class="modal-detail-row">
      <strong>Estimated Budget:</strong>
      <p>${esc(card.estimated_budget || "Calculated based on selected stay and transit.")}</p>
    </div>
    <div class="modal-detail-row">
      <strong>Travel Accessibility:</strong>
      <p>${esc(card.travel_time)}</p>
    </div>
    <div class="modal-detail-row source-row">
      <strong>Evidence Source:</strong>
      ${card.source_url ? `<a href="${esc(card.source_url)}" target="_blank" rel="noreferrer" class="source-link">${esc(card.source || "Official Source")} ↗</a>` : `<span>${esc(card.source || "Verified travel scout")}</span>`}
    </div>
  `;

  modal.classList.remove("hidden");
}

discover$("closeModalBtn")?.addEventListener("click", () => {
  discover$("detailsModal").classList.add("hidden");
});

discover$("detailsModal")?.addEventListener("click", event => {
  if (event.target === discover$("detailsModal")) {
    discover$("detailsModal").classList.add("hidden");
  }
});

discover$("discoverCancelBtn")?.addEventListener("click", () => {
  discoveryController?.abort();
});

// SUBMISSION HANDLER
discover$("discoveryForm").onsubmit = async event => {
  event.preventDefault();
  const origin = discover$("discoverOrigin").value.trim();
  const month = discover$("discoverMonth").value;
  const year = Number(discover$("discoverYear").value) || 2026;
  const dates = getDiscoveryDates();
  const days = dates?.days;
  const pace = discover$("discoverPace")?.value || "balanced";
  const budget = discover$("discoverBudget")?.value === "flexible" ? null : Number(discover$("discoverBudget")?.value);

  if (!origin) {
    alert("Please enter a starting location.");
    return;
  }
  if (!dates || !Number.isInteger(days) || days < 1 || days > 30) {
    alert("Please select a valid start and end date within 30 days.");
    return;
  }

  const submitBtn = discover$("discoverSubmitBtn");
  const cancelBtn = discover$("discoverCancelBtn");
  discoveryController = new AbortController();
  submitBtn.disabled = true;
  cancelBtn.classList.remove("hidden");
  submitBtn.innerHTML = '<span class="loader"></span> Scouting Destinations...';

  const progressSection = discover$("discoverProgress");
  const statusContainer = discover$("discoverStatus");
  const resultsSection = discover$("discoverResults");

  progressSection.classList.remove("hidden");
  resultsSection.classList.add("hidden");

  statusContainer.innerHTML = `
    <div class="progress-active"><span><span class="loader"></span></span> 1. Nearby Destination Agent: Scouting destinations near ${esc(origin)} for ${days} days...</div>
    <div><span>⋯</span> 2. Seasonal Event Agent: Verifying events for ${esc(month)} ${year}...</div>
    <div><span>⋯</span> 3. Weather Agent: Analyzing monthly climate expectations...</div>
    <div><span>⋯</span> 4. Destination Research Agent: Checking travel connectivity & attractions...</div>
    <div><span>⋯</span> 5. Destination Ranking Agent: Gemini synthesizing 5–8 clean recommendation cards...</div>
  `;

  try {
    const payload = {
      origin,
      month,
      year,
      start_date: dates.startDate,
      end_date: dates.endDate,
      days,
      interests: Array.from(selectedInterests),
      pace,
      budget,
      mood: Array.from(selectedInterests).slice(0, 2).join(" + ") || "Nature & Culture"
    };

    const response = await fetch("/api/discover", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: discoveryController.signal
    });

    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error(data.error || "Destination research failed.");
    }

    statusContainer.innerHTML = `
      <div><span>✓</span> 1. Nearby destinations scouted</div>
      <div><span>✓</span> 2. Seasonal events verified for ${esc(month)} ${year}</div>
      <div><span>✓</span> 3. Weather & climate analyzed</div>
      <div><span>✓</span> 4. Travel connectivity & attractions verified</div>
      <div><span>✓</span> 5. 5–8 clean destination cards ranked</div>
    `;

    currentCandidateCards = data.destinations || [];
    discover$("discoverNote").textContent = data.scout_summary || `Top recommendations for ${month} ${year} from ${origin}.`;
    
    renderCards();
    resultsSection.classList.remove("hidden");
    resultsSection.scrollIntoView({ behavior: "smooth", block: "start" });

  } catch (error) {
    statusContainer.innerHTML = error.name === "AbortError"
      ? '<div class="error">Research paused. You can start it again when ready.</div>'
      : `<div class="error">${esc(error.message)}</div>`;
  } finally {
    submitBtn.disabled = false;
    submitBtn.innerHTML = 'Scout Destinations <span>→</span>';
    cancelBtn.classList.add("hidden");
    discoveryController = null;
  }
};

// CONTINUE TO TRIP PLANNING
discover$("continuePlanningBtn").onclick = () => {
  if (selectedDestinations.size === 0) {
    alert("Please select at least one destination by clicking '+ Add to Trip'.");
    return;
  }
  goToTripPlanner(Array.from(selectedDestinations.values()));
};
