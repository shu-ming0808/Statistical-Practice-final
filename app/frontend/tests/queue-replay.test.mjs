import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const replay = JSON.parse(readFileSync(new URL("../public/data/queue-replay.json", import.meta.url), "utf8"));
const forecast = JSON.parse(readFileSync(new URL("../public/data/research.json", import.meta.url), "utf8"));

test("retrospective replay matches the published forecast cohort and discloses assumptions", () => {
  assert.equal(replay.schema_version, 1);
  assert.equal(replay.events.length, 9);
  assert.equal(replay.sensitivity.length, 81);
  assert.equal(replay.assumptions.actual_is_hindsight, true);
  assert.equal(replay.assumptions.historical_queue_reconstruction, false);
  assert.deepEqual(replay.events.map(row => row.event_date),
    [...new Set(forecast.model.hourly.map(row => row.event_date))].sort());
  for (const event of replay.events) {
    const rows = forecast.model.hourly.filter(row => row.event_date === event.event_date);
    assert.equal(event.predicted_demand, Math.round(rows.reduce((sum, row) => sum + row.predicted, 0)));
    assert.equal(event.observed_demand, Math.round(rows.reduce((sum, row) => sum + row.actual, 0)));
    assert.equal(event.wait_person_minutes_difference,
      event.hindsight_wait_person_minutes - event.predicted_wait_person_minutes);
    assert.equal(replay.sensitivity.filter(row => row.event_date === event.event_date).length, 9);
  }
});
