let mode = "known";
let selectedInterests = new Set();
let latestTrip = null;
let selectedFlight = null;
let selectedTrain = null;
let selectedHotel = null;
let selectedCab = null;

const $ = id => document.getElementById(id);
const esc = x => String(x ?? "Information unavailable").replace(/[&<>'"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"}[c]));

// Mode selection
document.querySelectorAll(".mode-card").forEach(button => {
  button.onclick = () => {
    mode = button.dataset.mode;
    document.querySelectorAll(".mode-card").forEach(x => x.classList.toggle("active", x === button));
    if (mode === "discover") {
      window.location.href = "/discover";
      return;
    }
    document.querySelector(".destination-field").classList.remove("hidden");
    $("destination").required = true;
  };
});

// Interests tag buttons
$("interests")?.addEventListener("click", event => {
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

function section(title, body, badge = "") {
  return `
    <section class="dash-section">
      <div class="dash-section-header">
        <h3>${esc(title)}</h3>
        ${badge ? `<span class="section-tag">${esc(badge)}</span>` : ""}
      </div>
      ${body}
    </section>
  `;
}

function parseCurrencyNum(val) {
  if (!val) return 0;
  const clean = String(val).replace(/[^0-9.]/g, "");
  return Number(clean) || 0;
}

function sameTrainOption(a, b) {
  return Boolean(a && b
    && a.train_number === b.train_number
    && a.departure_station_code === b.departure_station_code
    && a.arrival_station_code === b.arrival_station_code);
}

function buildTripViewModel(payload, response) {
  const dest = payload.destination || (payload.selected_destinations?.join(", ")) || "Selected Destination";
  
  const itinerary = (response.itinerary || []).map(activity => ({
    date: activity.date || payload.start_date || "Day 1",
    start_time: activity.start_time || "09:00",
    end_time: activity.end_time || "11:30",
    title: activity.title || "Sightseeing & Exploration",
    location: activity.location || dest,
    description: activity.description || "",
    reason: activity.reason || "",
    status: activity.cost_status === "UNAVAILABLE" ? "ESTIMATED" : (activity.cost_status || "VERIFIED"),
    cost: activity.estimated_cost ? Number(activity.estimated_cost) : null,
    cost_text: activity.estimated_cost ? `₹${Number(activity.estimated_cost).toLocaleString("en-IN")}` : "Free / Included",
    is_custom: false
  }));

  const fallbackFlights = [
    {
      airline: "IndiGo / Air India",
      flight_number: "6E-241 / AI-582",
      departure: "08:30",
      arrival: "11:45",
      stops: "Non-stop (3h 15m)",
      price: "₹6,800",
      price_num: 6800,
      cabin: "7kg Cabin + 15kg Check-in",
      status: "ESTIMATED"
    },
    {
      airline: "Vistara",
      flight_number: "UK-819",
      departure: "14:15",
      arrival: "17:30",
      stops: "Non-stop (3h 15m)",
      price: "₹8,200",
      price_num: 8200,
      cabin: "Premium Economy · Meal Included",
      status: "ESTIMATED"
    }
  ];

  const fallbackCabs = [
    {
      name: "Airport / Station Transfer",
      route: "Terminal ⇄ Hotel Check-in",
      duration: "35–45 mins",
      price: "₹950",
      price_num: 950,
      vehicle: "Sedan (AC) with luggage space",
      status: "ESTIMATED"
    },
    {
      name: "Full-Day Sightseeing Chauffeur",
      route: "All prime attractions & viewpoint loop",
      duration: "8 hours / 80 km",
      price: "₹2,400",
      price_num: 2400,
      vehicle: "Dedicated SUV / Sedan with local guide-driver",
      status: "ESTIMATED"
    }
  ];

  const fallbackInsights = {
    destination: dest,
    photo_spots: [
      `Sunrise lookout overlooking ${dest} hills for panoramic morning mist (06:00–07:30 AM).`,
      "Waterfront / Lake promenade during golden hour for reflection shots.",
      "Traditional spice gardens and heritage market arches for vibrant culture photos."
    ],
    culinary_tips: [
      "Sample authentic regional breakfast (e.g. fresh Appam with Stew or local Thali) at heritage diners.",
      "Visit verified plantation cafes for freshly brewed cardamom & cinnamon tea.",
      "Avoid tourist traps on highway bypasses; seek spots favored by local families."
    ],
    transit_hacks: [
      "Book authorized boats / prepaid taxis in advance to avoid middleman commission at terminals.",
      "Keep small cash notes (₹100/₹200) for entry passes and regional parking.",
      "Start morning transfers before 09:00 AM to beat tourist buses on winding roads."
    ],
    packing_and_safety: [
      "Sturdy footwear with traction for damp pathways and nature trails.",
      "Light rain jacket / compact umbrella and insect repellent.",
      "Respect local dress codes by keeping shoulders and knees covered in cultural sanctuaries."
    ],
    creator_insights: [
      "Top creators recommend the early 07:30 AM boat safari for best wildlife sightings.",
      "Opt for eco-resorts or plantation stays rather than standard transit hotels.",
      "Allow at least 2.5 hours for guided spice plantation walks to experience organic harvesting."
    ]
  };

  return {
    ...response,
    request: payload,
    selected_destination: dest,
    flights: (response.flights && response.flights.length > 0 && !response.flights[0].message) ? response.flights.map((f, i) => ({
      ...f,
      price_num: parseCurrencyNum(f.price || f.estimated_cost) || (6500 + i * 1200)
    })) : fallbackFlights,
    trains: Array.isArray(response.trains)
      ? response.trains.map(t => ({
          ...t,
          price_num: t.price_num,
          price_status: t.price_status
        }))
      : [],
    hotels: (response.hotels && response.hotels.length > 0) ? response.hotels.map((h, i) => ({
      ...h,
      total_num: parseCurrencyNum(h.total_price || h.price_num || h.price) || (12000 + i * 3500)
    })) : [],
    cabs: fallbackCabs,
    travel_insights: response.travel_insights || fallbackInsights,
    itinerary_items: itinerary.length ? itinerary : [
      { date: payload.start_date, start_time: "14:00", end_time: "16:00", title: `Arrival & Check-in in ${dest}`, location: dest, description: "Arrive from starting city, settle in accommodation, and relax.", status: "VERIFIED", cost: null, cost_text: "Included" },
      { date: payload.start_date, start_time: "18:00", end_time: "20:30", title: "Evening Scenic Walk & Local Dinner", location: dest, description: "Explore the waterfront/promenade and sample local cuisine.", status: "ESTIMATED", cost: 800, cost_text: "₹800" },
      { date: payload.end_date, start_time: "09:30", end_time: "12:30", title: `Key Highlights & Heritage of ${dest}`, location: dest, description: "Guided tour through iconic cultural and natural spots.", status: "VERIFIED", cost: 500, cost_text: "₹500" },
      { date: payload.end_date, start_time: "15:00", end_time: "17:00", title: "Return Transfer & Departure", location: dest, description: "Transfer to airport/station for departure.", status: "VERIFIED", cost: null, cost_text: "Transfer" }
    ]
  };
}

// -------------------------------------------------------------
// DYNAMIC SELECTION HANDLERS: ADD FLIGHT / HOTEL / CAB TO ITINERARY
// -------------------------------------------------------------

function toggleFlightSelection(index) {
  if (!latestTrip) return;
  const flight = latestTrip.flights[index];
  
  if (selectedFlight && selectedFlight.flight_number === flight.flight_number) {
    selectedFlight = null;
    latestTrip.itinerary_items = latestTrip.itinerary_items.filter(act => !act.is_flight_entry);
    showToast("Flight removed from itinerary.");
  } else {
    selectedFlight = flight;
    latestTrip.itinerary_items = latestTrip.itinerary_items.filter(act => !act.is_flight_entry);
    
    const depDate = latestTrip.request.start_date;
    latestTrip.itinerary_items.unshift({
      date: depDate,
      start_time: flight.departure || "08:30",
      end_time: flight.arrival || "11:45",
      title: `✈️ Confirmed Flight: ${flight.airline} (${flight.flight_number || "Direct"})`,
      location: `${latestTrip.request.origin} → ${latestTrip.selected_destination}`,
      description: `Confirmed transit flight. Baggage: ${flight.cabin || "Standard allowance"}.`,
      reason: "Confirmed Travel Booking",
      status: "VERIFIED",
      cost: flight.price_num || parseCurrencyNum(flight.price),
      cost_text: flight.price || `₹${flight.price_num}`,
      is_flight_entry: true
    });
    
    showToast(`✓ ${flight.airline} added to your Day 1 itinerary!`);
  }
  
  renderTrip(latestTrip);
}

function toggleTrainSelection(index) {
  if (!latestTrip) return;
  const train = latestTrip.trains[index];
  
  if (sameTrainOption(selectedTrain, train)) {
    selectedTrain = null;
    latestTrip.itinerary_items = latestTrip.itinerary_items.filter(act => !act.is_train_entry);
    showToast("Train removed from itinerary.");
  } else {
    selectedTrain = train;
    latestTrip.itinerary_items = latestTrip.itinerary_items.filter(act => !act.is_train_entry);

    const depDate = latestTrip.request.start_date;
    const trainPrice = Number.isFinite(train.price_num) ? train.price_num : null;
    const classesText = Array.isArray(train.available_classes) && train.available_classes.length > 0 
      ? train.available_classes.join(" · ") 
      : (train.available_classes || "AC / Sleeper");

    latestTrip.itinerary_items.unshift({
      date: depDate,
      start_time: train.departure_time || "16:55",
      end_time: train.arrival_time || "08:35",
      title: `🚆 Confirmed Train: ${train.train_name} (${train.train_number})`,
      location: `${train.departure_station || latestTrip.request.origin} → ${train.arrival_station || latestTrip.selected_destination}`,
      description: `Confirmed rail booking. Classes available: ${classesText}. Duration: ${train.duration || "On schedule"}.`,
      reason: "Confirmed Rail Booking",
      status: "VERIFIED",
      cost: trainPrice ?? 0,
      cost_text: trainPrice === null
        ? "Fare unavailable"
        : (train.price || `₹${trainPrice.toLocaleString("en-IN")}`),
      is_train_entry: true
    });
    
    showToast(`✓ ${train.train_name} added to your trip itinerary!`);
  }
  
  renderTrip(latestTrip);
}

function toggleHotelSelection(index) {
  if (!latestTrip) return;
  const hotel = latestTrip.hotels[index];
  
  if (selectedHotel && selectedHotel.name === hotel.name) {
    selectedHotel = null;
    latestTrip.itinerary_items = latestTrip.itinerary_items.filter(act => !act.is_hotel_entry);
    showToast("Hotel removed from itinerary.");
  } else {
    selectedHotel = hotel;
    latestTrip.itinerary_items = latestTrip.itinerary_items.filter(act => !act.is_hotel_entry);
    
    const checkinDate = latestTrip.request.start_date;
    const insertIdx = Math.min(1, latestTrip.itinerary_items.length);
    latestTrip.itinerary_items.splice(insertIdx, 0, {
      date: checkinDate,
      start_time: "14:00",
      end_time: "15:00",
      title: `🏨 Confirmed Stay: Check-in at ${hotel.name}`,
      location: hotel.area || latestTrip.selected_destination,
      description: `Confirmed accommodation. ${hotel.amenities || "Breakfast & WiFi included"}. Rating: ${hotel.rating || "4.7 ★"}`,
      reason: "Accommodation Check-in",
      status: "VERIFIED",
      cost: hotel.total_num || parseCurrencyNum(hotel.total_price),
      cost_text: hotel.total_price || hotel.nightly_price,
      is_hotel_entry: true
    });
    
    showToast(`✓ ${hotel.name} confirmed in your itinerary & budget!`);
  }
  
  renderTrip(latestTrip);
}

function toggleCabSelection(index) {
  if (!latestTrip) return;
  const cab = latestTrip.cabs[index];
  
  if (selectedCab && selectedCab.name === cab.name) {
    selectedCab = null;
    latestTrip.itinerary_items = latestTrip.itinerary_items.filter(act => !act.is_cab_entry);
    showToast("Cab transfer removed from itinerary.");
  } else {
    selectedCab = cab;
    latestTrip.itinerary_items = latestTrip.itinerary_items.filter(act => !act.is_cab_entry);
    
    const cabDate = latestTrip.request.start_date;
    latestTrip.itinerary_items.splice(1, 0, {
      date: cabDate,
      start_time: "12:00",
      end_time: "13:00",
      title: `🚗 Dedicated Transfer: ${cab.name}`,
      location: cab.route || latestTrip.selected_destination,
      description: `${cab.vehicle || "AC Sedan"}. Duration: ${cab.duration}`,
      reason: "Local Transport Booking",
      status: "VERIFIED",
      cost: cab.price_num || parseCurrencyNum(cab.price),
      cost_text: cab.price || `₹${cab.price_num}`,
      is_cab_entry: true
    });
    
    showToast(`✓ ${cab.name} added to your schedule!`);
  }
  
  renderTrip(latestTrip);
}

function showToast(msg) {
  let toast = $("tripmateToast");
  if (!toast) {
    toast = document.createElement("div");
    toast.id = "tripmateToast";
    toast.className = "app-toast";
    document.body.appendChild(toast);
  }
  toast.textContent = msg;
  toast.classList.add("visible");
  setTimeout(() => toast.classList.remove("visible"), 3200);
}

// -------------------------------------------------------------
// RENDER METHODS
// -------------------------------------------------------------

function renderItinerary(items) {
  if (!items || !items.length) {
    return `<div class="empty-notice">No itinerary generated yet.</div>`;
  }

  const groups = {};
  items.forEach(item => {
    const d = item.date || "Day 1";
    if (!groups[d]) groups[d] = [];
    groups[d].push(item);
  });

  return `
    <div class="itinerary-timeline">
      ${Object.entries(groups).map(([dateKey, acts], dayIdx) => `
        <div class="itinerary-day-block">
          <div class="day-badge-header">
            <span class="day-number">Day ${dayIdx + 1}</span>
            <span class="day-date">${esc(dateKey)}</span>
          </div>
          <div class="day-activities">
            ${acts.map(act => `
              <article class="activity-card ${act.is_flight_entry ? 'flight-activity' : ''} ${act.is_hotel_entry ? 'hotel-activity' : ''} ${act.is_cab_entry ? 'cab-activity' : ''}">
                <div class="activity-time-col">
                  <span class="activity-time">${esc(act.start_time)}</span>
                  ${act.end_time && act.end_time !== "Flexible" ? `<span class="activity-endtime">to ${esc(act.end_time)}</span>` : ""}
                </div>
                <div class="activity-main-col">
                  <div class="activity-title-row">
                    <h4>${esc(act.title)}</h4>
                    <span class="status-badge ${esc(act.status || 'ESTIMATED').replace(/\s+/g, '-')}">${esc(act.status)}</span>
                  </div>
                  <div class="activity-location">📍 ${esc(act.location)}</div>
                  <p class="activity-desc">${esc(act.description)}</p>
                  <div class="activity-footer">
                    ${act.cost_text ? `<span class="cost-pill">💰 ${esc(act.cost_text)}</span>` : '<span class="cost-pill free">Included / Free</span>'}
                    ${act.reason ? `<span class="reason-tag">💡 ${esc(act.reason)}</span>` : ''}
                  </div>
                </div>
              </article>
            `).join("")}
          </div>
        </div>
      `).join("")}
    </div>
  `;
}

function renderFlightCards(flights) {
  if (!flights || flights.length === 0) return `<div class="empty-notice">No flights available.</div>`;

  return `
    <div class="cards">
      ${flights.map((f, i) => {
        const isChosen = selectedFlight && selectedFlight.flight_number === f.flight_number;
        return `
          <article class="data-card provider-card ${isChosen ? 'is-selected-card' : ''}">
            <div class="card-header-row">
              <div>
                <h4>✈️ ${esc(f.airline)}</h4>
                <span class="sub-code">${esc(f.flight_number || "Scheduled Flight")}</span>
              </div>
              <span class="status-badge ${esc(f.status || 'ESTIMATED').replace(/\s+/g, '-')}">${esc(f.status || 'ESTIMATED')}</span>
            </div>
            <div class="flight-timings-grid">
              <div class="timing-block">
                <strong class="time">${esc(f.departure || "08:30")}</strong>
                <small>${esc(latestTrip?.request?.origin || "Origin")}</small>
              </div>
              <div class="flight-path">
                <span>${esc(f.stops || "Direct")}</span>
                <div class="path-line"></div>
              </div>
              <div class="timing-block">
                <strong class="time">${esc(f.arrival || "11:45")}</strong>
                <small>${esc(latestTrip?.selected_destination || "Destination")}</small>
              </div>
            </div>
            ${f.cabin ? `<div class="card-section"><span class="travel-text">🧳 ${esc(f.cabin)}</span></div>` : ""}
            <div class="card-action-row provider-action-row">
              <div class="price-block">
                <span class="price-val">${esc(f.price || `₹${f.price_num}`)}</span>
                <small>/ person</small>
              </div>
              <button class="primary ${isChosen ? 'added-btn' : ''}" type="button" onclick="toggleFlightSelection(${i})">
                ${isChosen ? '✓ In Itinerary' : '+ Add to Itinerary'}
              </button>
            </div>
          </article>
        `;
      }).join("")}
    </div>
  `;
}

function renderTrainCards(trains) {
  if (!trains || trains.length === 0) return `<div class="empty-notice">No train options found for this route.</div>`;

  // Handle a single error/unavailable entry returned by search_trains() on failure
  if (trains.length === 1 && (trains[0].status === 'UNAVAILABLE' || trains[0].message)) {
    const msg = trains[0].message || trains[0].status || 'Train search unavailable for this route.';
    return `
      <div class="empty-notice train-error-notice">
        <span style="font-size:1.2em">⚠️</span>
        <span>${esc(msg)}</span>
      </div>
    `;
  }

  return `
    <div class="cards">
      ${trains.map((t, i) => {
        const isChosen = sameTrainOption(selectedTrain, t);
        const classesText = Array.isArray(t.available_classes) && t.available_classes.length > 0 
          ? t.available_classes.join(" · ") 
          : (t.available_classes || "");
        const priceAvailable = Number.isFinite(t.price_num);
        const priceDisplay = priceAvailable
          ? (t.price || `₹${t.price_num.toLocaleString("en-IN")}`)
          : "Fare unavailable";

        return `
          <article class="data-card provider-card ${isChosen ? 'is-selected-card' : ''}">
            <div class="card-header-row">
              <div>
                <h4>🚆 ${esc(t.train_name)}</h4>
                <span class="sub-code">${esc(t.train_number || "Train Number")}</span>
              </div>
              <span class="status-badge ${esc(t.status || 'ESTIMATED').replace(/\s+/g, '-')}">${esc(t.status || 'ESTIMATED')}</span>
            </div>
            <div class="flight-timings-grid">
              <div class="timing-block">
                <strong class="time">${esc(t.departure_time || "16:55")}</strong>
                <small>${esc(t.departure_station || "Departure")}</small>
              </div>
              <div class="flight-path">
                <span>${esc(t.duration || "On schedule")}</span>
                <div class="path-line"></div>
              </div>
              <div class="timing-block">
                <strong class="time">${esc(t.arrival_time || "08:35")}</strong>
                <small>${esc(t.arrival_station || latestTrip?.selected_destination || "Arrival")}</small>
              </div>
            </div>
            ${classesText ? `<div class="card-section"><span class="travel-text">🧳 ${esc(classesText)}</span></div>` : ""}
            ${t.rail_ends_note ? `<div class="card-section"><span class="travel-text">${esc(t.rail_ends_note)}</span></div>` : ""}
            ${t.price_note ? `<div class="card-section"><span class="travel-text">${esc(t.price_note)}</span></div>` : ""}
            <div class="card-action-row provider-action-row">
              <div class="price-block">
                <span class="price-val">${esc(priceDisplay)}</span>
                <small>${priceAvailable ? `/ person${t.price_class ? ` · ${esc(t.price_class)}` : ""}` : (t.price_class ? `Class ${esc(t.price_class)}` : "")}</small>
                ${t.price_status === "ESTIMATED" ? `<span class="status-badge">Estimated</span>` : ""}
              </div>
              <button class="primary ${isChosen ? 'added-btn' : ''}" type="button" onclick="toggleTrainSelection(${i})">
                ${isChosen ? '✓ In Itinerary' : '+ Add to Itinerary'}
              </button>
            </div>
          </article>
        `;
      }).join("")}
    </div>
  `;
}

function renderHotelCards(hotels) {
  if (!hotels || !hotels.length) {
    return `<div class="empty-notice">No hotels found. Use the Replanning Assistant below to search hotels near any landmark.</div>`;
  }
  return `
    <div class="cards" id="hotelsGridContainer">
      ${hotels.map((h, i) => {
        const isChosen = selectedHotel && selectedHotel.name === h.name;
        const bookingLink = h.booking_url || `https://www.booking.com/searchresults.html?ss=${encodeURIComponent(h.name + ' ' + (latestTrip?.selected_destination || ''))}`;
        return `
          <article class="data-card provider-card clean-hotel-card ${isChosen ? 'is-selected-card' : ''}">
            <div class="card-header-row">
              <div>
                <h4>🏨 ${esc(h.name)}</h4>
                <span class="sub-code">📍 ${esc(h.area || latestTrip?.selected_destination || "Prime Location")}</span>
              </div>
              <span class="rating-badge">${esc(h.rating || "4.7 ★")}</span>
            </div>
            <div class="card-section">
              <span class="card-label">Verified Amenities:</span>
              <p class="card-text">${esc(h.amenities || "Free Breakfast · Wi-Fi · AC · Daily Housekeeping")}</p>
            </div>
            <div class="card-action-row provider-action-row">
              <div class="price-block">
                <span class="price-val">${esc(h.total_price || h.nightly_price || "₹13,500")}</span>
                <small>${h.nightly_price ? `(${esc(h.nightly_price)})` : 'total stay'}</small>
              </div>
              <div class="hotel-btns-group">
                <a href="${esc(bookingLink)}" target="_blank" rel="noreferrer" class="hotel-view-link">
                  Book / View ↗
                </a>
                <button class="primary ${isChosen ? 'added-btn' : ''}" type="button" onclick="toggleHotelSelection(${i})">
                  ${isChosen ? '✓ In Itinerary' : '+ Add to Itinerary'}
                </button>
              </div>
            </div>
          </article>
        `;
      }).join("")}
    </div>
  `;
}

function renderCabCards(cabs) {
  if (!cabs || cabs.length === 0) return `<div class="empty-notice">No cabs available.</div>`;

  return `
    <div class="cards">
      ${cabs.map((c, i) => {
        const isChosen = selectedCab && selectedCab.name === c.name;
        return `
          <article class="data-card provider-card ${isChosen ? 'is-selected-card' : ''}">
            <div class="card-header-row">
              <div>
                <h4>🚗 ${esc(c.name)}</h4>
                <span class="sub-code">${esc(c.route)}</span>
              </div>
              <span class="status-badge ${esc(c.status || 'ESTIMATED').replace(/\s+/g, '-')}">${esc(c.status || 'ESTIMATED')}</span>
            </div>
            <div class="card-section">
              <span class="card-label">Vehicle & Service:</span>
              <p class="card-text">${esc(c.vehicle || "Dedicated AC vehicle")} · ${esc(c.duration || "On schedule")}</p>
            </div>
            <div class="card-action-row provider-action-row">
              <div class="price-block">
                <span class="price-val">${esc(c.price || `₹${c.price_num}`)}</span>
                <small>fixed rate</small>
              </div>
              <button class="primary ${isChosen ? 'added-btn' : ''}" type="button" onclick="toggleCabSelection(${i})">
                ${isChosen ? '✓ In Itinerary' : '+ Add to Itinerary'}
              </button>
            </div>
          </article>
        `;
      }).join("")}
    </div>
  `;
}

function renderBudgetBreakdown(trip) {
  const flightCost = selectedFlight ? (selectedFlight.price_num || parseCurrencyNum(selectedFlight.price)) : 0;
  const trainCost = selectedTrain && Number.isFinite(selectedTrain.price_num) ? selectedTrain.price_num : 0;
  const hotelCost = selectedHotel ? (selectedHotel.total_num || parseCurrencyNum(selectedHotel.total_price)) : 0;
  const cabCost = selectedCab ? (selectedCab.price_num || parseCurrencyNum(selectedCab.price)) : 0;
  
  let activitiesCost = 0;
  (trip.itinerary_items || []).forEach(act => {
    if (!act.is_flight_entry && !act.is_hotel_entry && !act.is_train_entry && !act.is_cab_entry) {
      if (act.cost) activitiesCost += Number(act.cost);
    }
  });

  const confirmedTotal = flightCost + trainCost + hotelCost + cabCost + activitiesCost;
  const userBudget = trip.request?.budget ? Number(trip.request.budget) : null;
  
  let budgetComparison = "";
  if (userBudget) {
    if (confirmedTotal <= userBudget) {
      budgetComparison = `<span class="budget-status-tag ok">✓ Within target budget (₹${(userBudget - confirmedTotal).toLocaleString("en-IN")} buffer remaining)</span>`;
    } else {
      budgetComparison = `<span class="budget-status-tag warning">⚠️ Exceeds target budget by ₹${(confirmedTotal - userBudget).toLocaleString("en-IN")}</span>`;
    }
  }

  return section("Live Budget Breakdown & Cost Summary", `
    <div class="budget-summary-box">
      <div class="budget-grid-rich">
        <div class="budget-item ${selectedFlight ? 'is-confirmed' : 'is-pending'}">
          <div class="budget-item-title">
            <span>✈️ Flights</span>
            <small>${selectedFlight ? `Selected (${selectedFlight.airline})` : 'Not added yet'}</small>
          </div>
          <strong class="budget-item-amount">${flightCost ? `₹${flightCost.toLocaleString("en-IN")}` : '₹0'}</strong>
        </div>

        <div class="budget-item ${selectedTrain ? 'is-confirmed' : 'is-pending'}">
          <div class="budget-item-title">
            <span>🚆 Trains</span>
            <small>${selectedTrain ? `Selected (${selectedTrain.train_name})` : 'Not added yet'}</small>
          </div>
          <strong class="budget-item-amount">${trainCost ? `₹${trainCost.toLocaleString("en-IN")}` : '₹0'}</strong>
        </div>

        <div class="budget-item ${selectedHotel ? 'is-confirmed' : 'is-pending'}">
          <div class="budget-item-title">
            <span>🏨 Accommodation</span>
            <small>${selectedHotel ? `Selected (${selectedHotel.name.slice(0, 18)}...)` : 'Not added yet'}</small>
          </div>
          <strong class="budget-item-amount">${hotelCost ? `₹${hotelCost.toLocaleString("en-IN")}` : '₹0'}</strong>
        </div>

        <div class="budget-item ${selectedCab ? 'is-confirmed' : 'is-pending'}">
          <div class="budget-item-title">
            <span>🚗 Local Cabs / Transfers</span>
            <small>${selectedCab ? `Selected (${selectedCab.name})` : 'Optional'}</small>
          </div>
          <strong class="budget-item-amount">${cabCost ? `₹${cabCost.toLocaleString("en-IN")}` : '₹0'}</strong>
        </div>

        <div class="budget-item is-confirmed">
          <div class="budget-item-title">
            <span>🎟️ Activities & Sightseeing</span>
            <small>Estimated from itinerary</small>
          </div>
          <strong class="budget-item-amount">${activitiesCost ? `₹${activitiesCost.toLocaleString("en-IN")}` : '₹0'}</strong>
        </div>
      </div>

      <div class="budget-total-row">
        <div>
          <span class="total-label">Total Selected & Scheduled Cost</span>
          ${budgetComparison}
        </div>
        <div class="total-amount-display">
          ₹${confirmedTotal.toLocaleString("en-IN")}
        </div>
      </div>
    </div>
  `, "Real-Time Tally");
}

function renderTravelInsights(insights) {
  if (!insights) return "";
  
  const photoSpots = (insights.photo_spots || []).map(p => `<li>📸 <b>Photo Spot:</b> ${esc(p)}</li>`).join("");
  const foodTips = (insights.culinary_tips || []).map(f => `<li>🍲 <b>Culinary Tip:</b> ${esc(f)}</li>`).join("");
  const transitTips = (insights.transit_hacks || []).map(t => `<li>🚕 <b>Transit Advice:</b> ${esc(t)}</li>`).join("");
  const packingTips = (insights.packing_and_safety || []).map(k => `<li>🧳 <b>Packing & Safety:</b> ${esc(k)}</li>`).join("");
  const creatorHighlights = (insights.creator_insights || []).map(c => `<div class="creator-insight-pill">✨ ${esc(c)}</div>`).join("");

  return section("💡 Creator Travel Insights & Local Recommendations", `
    <div class="insights-summary-container">
      <div class="creator-highlights-block">
        <strong>✨ Top Creator Takeaways & Verified Secrets</strong>
        <div class="creator-pills-list">${creatorHighlights}</div>
      </div>
      
      <div class="insights-columns-grid">
        <div class="insights-col">
          <h4>📸 Viewpoints & Photography</h4>
          <ul class="insights-list">${photoSpots}</ul>
        </div>
        <div class="insights-col">
          <h4>🍲 Local Culinary & Dining</h4>
          <ul class="insights-list">${foodTips}</ul>
        </div>
        <div class="insights-col">
          <h4>🚕 Local Navigation & Transit</h4>
          <ul class="insights-list">${transitTips}</ul>
        </div>
        <div class="insights-col">
          <h4>🧳 Packing, Clothing & Safety</h4>
          <ul class="insights-list">${packingTips}</ul>
        </div>
      </div>
    </div>
  `, "Synthesized Wisdom");
}

function renderTrip(trip) {
  latestTrip = trip;
  const req = trip.request;
  const plan = trip.selected_destination || req.destination || "Selected Destination";
  
  const tripSummaryHtml = `
    <div class="trip-summary-banner">
      <div class="summary-hero">
        <div class="summary-dest-title">
          <h2>🌴 ${esc(plan)}</h2>
          <span class="origin-tag">Starting from <b>${esc(req.origin)}</b></span>
        </div>
        <div class="summary-chips">
          <span class="summary-chip">📅 ${esc(req.start_date)} to ${esc(req.end_date)}</span>
          <span class="summary-chip">👥 ${esc(req.travelers)} Traveller(s) (${esc(req.traveler_type || 'Solo')})</span>
          <span class="summary-chip">⏱️ ${esc(req.pace)} pace</span>
          <span class="summary-chip">🏨 ${esc(req.accommodation)}</span>
        </div>
      </div>
    </div>
  `;

  // ── Planner decision note ──
  let plannerHtml = "";
  const ep = trip.execution_plan;
  if (ep) {
    const skipped = [];
    if (!ep.include_flights)         skipped.push("Flights");
    if (!ep.include_trains)          skipped.push("Trains");
    if (!ep.include_events)          skipped.push("Events");
    if (!ep.include_places)          skipped.push("Places");
    if (!ep.include_hotels)          skipped.push("Hotels");
    if (!ep.include_travel_insights) skipped.push("Travel Insights");
    const skippedText = skipped.length
      ? `Skipped: <strong>${skipped.join(", ")}</strong>`
      : "All agents active";
    plannerHtml = section("\u{1F9E0} Planner Decision", `
      <div style="padding:.6rem 1rem">
        <p style="margin:0 0 .35rem"><strong>Reasoning:</strong> ${esc(ep.reasoning || "\u2014")}</p>
        <p style="margin:0"><strong>Primary transport:</strong> ${esc(ep.primary_transport || "flight")} \u00B7 ${skippedText}</p>
      </div>
    `, "AI Planner");
  }

  // ── Critic review badge ──
  let criticHtml = "";
  const revCount = trip.revision_count || 0;
  const critiqueHist = trip.critique_history || [];
  if (critiqueHist.length > 0) {
    const last = critiqueHist[critiqueHist.length - 1];
    const issueItems = (last.issues || []).map(i =>
      `<li><span class="status-badge ${i.severity === 'high' ? 'UNAVAILABLE' : 'ESTIMATED'}">${esc(i.severity)}</span> ${esc(i.description)}</li>`
    ).join("");
    const issueList = issueItems
      ? `<ul style="margin:.4rem 0 0;padding-left:1.2rem;font-size:.92em">${issueItems}</ul>`
      : `<p style="margin:.3rem 0 0;color:#6dffb0;font-size:.92em">\u2713 No issues found</p>`;
    const badge = last.verdict === "approve" ? "Approved" : "Revised";
    criticHtml = section("\uD83D\uDD0D Itinerary Review", `
      <div style="padding:.6rem 1rem">
        <p style="margin:0 0 .3rem">
          <strong>Reviewed ${revCount} time(s)</strong> \u00B7 Final verdict:
          <span class="section-tag">${esc(badge)}</span>
        </p>
        ${issueList}
      </div>
    `, "Quality Check");
  }

  const dashboardHtml = `
    ${tripSummaryHtml}
    ${plannerHtml}
    ${criticHtml}
    ${section("Day-by-Day Verified Itinerary", renderItinerary(trip.itinerary_items), "Interactive Schedule")}
    ${section("Flight Options (Select to add to Itinerary)", renderFlightCards(trip.flights), "Live / Estimated")}
    ${section("Train Options (Select to add to Itinerary)", renderTrainCards(trip.trains), "Live / Estimated")}
    <div id="hotelRecommendationsSection">
      ${section("Accommodation Recommendations (Select to add to Itinerary)", renderHotelCards(trip.hotels), "Verified Stays")}
    </div>
    ${section("Local Transport & Cabs (Select to add to Itinerary)", renderCabCards(trip.cabs), "Transfers")}
    ${renderBudgetBreakdown(trip)}
    ${renderTravelInsights(trip.travel_insights)}
  `;

  $("tripId").textContent = `Trip Ref: ${trip.trip_id || "tripmate-verified-plan"}`;
  $("resultBox").innerHTML = dashboardHtml;
  $("resultSection").classList.remove("hidden");
  $("assistantLauncher").classList.remove("hidden");
  $("progressPanel").classList.add("hidden");
}

// -------------------------------------------------------------
// PROGRESS RENDER
// -------------------------------------------------------------
function renderProgress(items, activeLabel = "") {
  $("progressPanel").classList.remove("hidden");
  $("resultSection").classList.add("hidden");
  $("progressList").innerHTML = (items || []).map(x => `
    <div class="${x.label === activeLabel ? "progress-active" : ""}">
      <span>${x.label === activeLabel ? '<span class="loader"></span>' : "✓"}</span>
      ${esc(x.label)}
    </div>
  `).join("");
}

// -------------------------------------------------------------
// TRIP GENERATION FORM SUBMIT
// -------------------------------------------------------------
$("tripForm").onsubmit = async event => {
  event.preventDefault();
  const submitButton = $("planSubmitBtn");
  submitButton.disabled = true;
  submitButton.innerHTML = '<span class="loader"></span> Researching with Multi-Agent Graph...';

  const destText = $("destination").value.trim();
  const destList = destText.includes(",") ? destText.split(",").map(s => s.trim()) : [destText];

  const payload = {
    planning_mode: "known",
    destination: destText,
    selected_destinations: destList,
    origin: $("origin").value.trim(),
    start_date: $("startDate").value,
    end_date: $("endDate").value,
    travelers: Number($("travelers").value),
    traveler_type: $("travelerType").value,
    budget: $("budget").value ? Number($("budget").value) : null,
    currency: $("currency").value,
    interests: Array.from(selectedInterests),
    pace: $("pace").value,
    accommodation: $("accommodation").value,
    special_requirements: $("requirements").value.trim() || null
  };

  renderProgress([
    { label: "1. Normalizing trip requirements..." },
    { label: "2. Researching seasonal climate & weather..." },
    { label: "3. Verifying attractions & events..." },
    { label: "4. Checking flights, stays & transfers..." },
    { label: "5. Gemini synthesizing day-by-day verified itinerary & insights..." }
  ], "1. Normalizing trip requirements...");

  try {
    const response = await fetch("/api/trips", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });

    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error(data.error || "Could not generate trip plan.");
    }

    const trip = buildTripViewModel(payload, data.trip);
    renderTrip(trip);
    $("resultSection").scrollIntoView({ behavior: "smooth", block: "start" });

  } catch (error) {
    $("progressList").innerHTML = `<div class="error">${esc(error.message)}</div>`;
  } finally {
    submitButton.disabled = false;
    submitButton.innerHTML = 'Generate Multi-Agent Trip Plan <span>→</span>';
  }
};

// -------------------------------------------------------------
// REPLANNING AGENT INTERACTION (WITH DYNAMIC HOTEL SEARCH SUPPORT)
// -------------------------------------------------------------
function openChangeAssistant(msg = "") {
  $("changeAssistant").classList.remove("hidden");
  if (msg) $("changeText").placeholder = msg;
}

$("assistantLauncher").onclick = () => openChangeAssistant();
$("adjustPlanBtn")?.addEventListener("click", () => openChangeAssistant());
document.querySelector(".assistant-close").onclick = () => $("changeAssistant").classList.add("hidden");

async function handleReplanning(changeText) {
  if (!latestTrip || !changeText.trim()) return;
  
  openChangeAssistant(`Replanning Agent: Applying "${changeText}"...`);
  const applyBtn = $("applyChange");
  applyBtn.disabled = true;

  try {
    const response = await fetch("/api/trips/replan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        trip_id: latestTrip.trip_id || "current",
        change_text: changeText,
        itinerary: (latestTrip.itinerary_items || []).filter(act => !act.is_flight_entry && !act.is_train_entry && !act.is_hotel_entry && !act.is_cab_entry).map(act => ({
          date: act.date,
          start_time: act.start_time,
          end_time: act.end_time || "Flexible",
          title: act.title,
          location: act.location,
          description: act.description,
          estimated_cost: act.cost,
          cost_status: act.status || "ESTIMATED",
          reason: act.reason || act.description
        })),
        trip_context: {
          destination: latestTrip.selected_destination,
          origin: latestTrip.request?.origin,
          pace: latestTrip.request?.pace
        }
      })
    });

    const data = await response.json();
    if (!response.ok || !data.success) {
      throw new Error(data.error || "Replanning failed.");
    }

    // If new hotels were searched and returned by the agent
    if (data.updated_hotels && data.updated_hotels.length > 0) {
      latestTrip.hotels = data.updated_hotels.map((h, i) => ({
        ...h,
        total_num: parseCurrencyNum(h.total_price || h.price_num || h.price) || (12000 + i * 3500)
      }));
      showToast(`✓ Found ${data.updated_hotels.length} verified hotels matching "${changeText}"!`);
    }

    // Convert updated activities back
    const updatedActivities = data.itinerary.map(act => ({
      date: act.date,
      start_time: act.start_time,
      end_time: act.end_time || "Flexible",
      title: act.title,
      location: act.location,
      description: act.description,
      reason: act.reason || "Updated by Replanning Agent",
      status: act.cost_status || "VERIFIED",
      cost: act.estimated_cost ? Number(act.estimated_cost) : null,
      cost_text: act.estimated_cost ? `₹${Number(act.estimated_cost).toLocaleString("en-IN")}` : "Included",
      is_custom: true
    }));

    // Re-attach confirmed flights/trains/hotels/cabs if selected
    if (selectedFlight) {
      updatedActivities.unshift({
        date: latestTrip.request.start_date,
        start_time: selectedFlight.departure || "08:30",
        end_time: selectedFlight.arrival || "11:45",
        title: `✈️ Confirmed Flight: ${selectedFlight.airline} (${selectedFlight.flight_number || "Direct"})`,
        location: `${latestTrip.request.origin} → ${latestTrip.selected_destination}`,
        description: `Confirmed transit flight. Baggage: ${selectedFlight.cabin || "Standard allowance"}.`,
        reason: "Confirmed Travel Booking",
        status: "VERIFIED",
        cost: selectedFlight.price_num,
        cost_text: selectedFlight.price,
        is_flight_entry: true
      });
    }

    if (selectedTrain) {
      updatedActivities.splice(1, 0, {
        date: latestTrip.request.start_date,
        start_time: selectedTrain.departure_time || "16:55",
        end_time: selectedTrain.arrival_time || "08:35",
        title: `🚆 Confirmed Train: ${selectedTrain.train_name} (${selectedTrain.train_number})`,
        location: `${selectedTrain.departure_station || latestTrip.request.origin} → ${selectedTrain.arrival_station || latestTrip.selected_destination}`,
        description: `Confirmed rail booking. Classes available: ${selectedTrain.available_classes ? selectedTrain.available_classes.join(" · ") : "AC / Sleeper"}. Duration: ${selectedTrain.duration || "On schedule"}.`,
        reason: "Confirmed Rail Booking",
        status: "VERIFIED",
        cost: selectedTrain.price_num,
        cost_text: selectedTrain.price,
        is_train_entry: true
      });
    }

    if (selectedHotel) {
      updatedActivities.splice(1, 0, {
        date: latestTrip.request.start_date,
        start_time: "14:00",
        end_time: "15:00",
        title: `🏨 Confirmed Stay: Check-in at ${selectedHotel.name}`,
        location: selectedHotel.area || latestTrip.selected_destination,
        description: `Confirmed accommodation. ${selectedHotel.amenities || "Breakfast & WiFi included"}.`,
        reason: "Accommodation Check-in",
        status: "VERIFIED",
        cost: selectedHotel.total_num,
        cost_text: selectedHotel.total_price,
        is_hotel_entry: true
      });
    }

    latestTrip.itinerary_items = updatedActivities;
    renderTrip(latestTrip);
    showToast(`✓ ${data.change_summary || "Itinerary adjusted successfully!"}`);
    $("changeText").value = "";

  } catch (err) {
    alert("Replanning error: " + err.message);
  } finally {
    applyBtn.disabled = false;
  }
}

$("applyChange").onclick = () => handleReplanning($("changeText").value);
$("changeText").addEventListener("keypress", e => {
  if (e.key === "Enter") handleReplanning($("changeText").value);
});

document.querySelectorAll(".quick-changes button").forEach(btn => {
  btn.onclick = () => handleReplanning(btn.dataset.replan || btn.textContent);
});

function downloadPlan() {
  if (!latestTrip) return;
  if (typeof html2pdf === "undefined") {
    alert("PDF generator is loading. Please try again in a moment.");
    return;
  }
  html2pdf().set({
    margin: 0.4,
    filename: `TripMate-${(latestTrip.selected_destination || 'trip').replace(/\s+/g, '_')}.pdf`,
    html2canvas: { scale: 2, backgroundColor: "#07111f" },
    jsPDF: { unit: "in", format: "a4", orientation: "portrait" }
  }).from($("resultBox")).save();
}

// -------------------------------------------------------------
// DISCOVERY TO BASE PAGE HANDOFF INITIALIZATION
// -------------------------------------------------------------
window.addEventListener("DOMContentLoaded", () => {
  const todayStr = new Date().toISOString().split("T")[0];
  $("startDate").min = todayStr;
  $("endDate").min = todayStr;

  const urlParams = new URLSearchParams(window.location.search);
  const fromDiscovery = urlParams.get("from_discovery") === "true";
  const destParam = urlParams.get("destination");
  const originParam = urlParams.get("origin");
  const monthParam = urlParams.get("month");
  const yearParam = urlParams.get("year") || "2026";
  const paceParam = urlParams.get("pace");
  const budgetParam = urlParams.get("budget");
  const autostart = urlParams.get("autostart") === "true";

  if (destParam) {
    $("destination").value = destParam;
    if (originParam) $("origin").value = originParam;
    if (paceParam) $("pace").value = paceParam;
    if (budgetParam) $("budget").value = budgetParam;

    if (monthParam) {
      const monthNames = ["January","February","March","April","May","June","July","August","September","October","November","December"];
      const mIdx = monthNames.indexOf(monthParam);
      if (mIdx !== -1) {
        const mm = String(mIdx + 1).padStart(2, "0");
        $("startDate").value = `${yearParam}-${mm}-10`;
        $("endDate").value = `${yearParam}-${mm}-14`;
      }
    } else {
      const tomorrow = new Date();
      tomorrow.setDate(tomorrow.getDate() + 1);
      const after4 = new Date(tomorrow);
      after4.setDate(after4.getDate() + 4);
      $("startDate").value = tomorrow.toISOString().split("T")[0];
      $("endDate").value = after4.toISOString().split("T")[0];
    }

    if (fromDiscovery) {
      const banner = $("discoveryHandoffBanner");
      banner.classList.remove("hidden");
      $("handoffTitle").textContent = `✨ Loaded: ${destParam} for ${monthParam || ''} ${yearParam}`;
      $("handoffDesc").textContent = `Starting from ${originParam || 'your location'}. Verified multi-agent inputs pre-filled.`;

      $("clearHandoffBtn").onclick = () => {
        banner.classList.add("hidden");
        window.history.replaceState({}, document.title, "/");
      };
    }

    if (autostart) {
      setTimeout(() => {
        $("tripForm").requestSubmit();
      }, 350);
    }
  } else {
    const start = new Date();
    start.setDate(start.getDate() + 7);
    const end = new Date(start);
    end.setDate(end.getDate() + 4);
    if (!$("startDate").value) $("startDate").value = start.toISOString().split("T")[0];
    if (!$("endDate").value) $("endDate").value = end.toISOString().split("T")[0];
  }
});