// Draws the price history as a step line: prices change at a moment and
// stay flat until the next check that sees a change.
(function () {
  var data = JSON.parse(document.getElementById("history").textContent);
  var css = getComputedStyle(document.documentElement);
  var ink = css.getPropertyValue("--ink").trim();
  var muted = css.getPropertyValue("--muted").trim();
  var rule = css.getPropertyValue("--rule").trim();
  // Same "3 Oct" style as the list below the chart, in Malaysia time.
  var months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  var dateFmt = {
    format: function (d) {
      var myt = new Date(d.getTime() + 8 * 3600 * 1000);
      return myt.getUTCDate() + " " + months[myt.getUTCMonth()];
    }
  };
  var xs = data.map(function (p) { return Date.parse(p.t); });
  var ringgit = function (sen) {
    return "RM " + (sen / 100).toLocaleString("en-MY", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  };

  new Chart(document.getElementById("chart"), {
    type: "line",
    data: {
      datasets: [{
        data: data.map(function (p) { return { x: Date.parse(p.t), y: p.price_sen }; }),
        stepped: true,
        borderColor: ink,
        borderWidth: 2,
        pointRadius: 0,
        pointHitRadius: 12
      }]
    },
    options: {
      maintainAspectRatio: false,
      animation: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            title: function (items) { return dateFmt.format(new Date(items[0].parsed.x)); },
            label: function (item) { return ringgit(item.parsed.y); }
          }
        }
      },
      scales: {
        x: {
          type: "linear",
          min: xs[0],
          max: xs[xs.length - 1],
          ticks: { color: muted, maxTicksLimit: 6, callback: function (v) { return dateFmt.format(new Date(v)); } },
          grid: { display: false },
          border: { color: rule }
        },
        y: {
          ticks: { color: muted, callback: function (v) { return ringgit(v); } },
          grid: { color: rule },
          border: { display: false }
        }
      }
    }
  });
})();
