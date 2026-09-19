# IDEAS

Parking lot for everything that is not Phase 1 through 6.

**Rule: nothing in this file goes into the code until Phase 7.** Write it down, close the file, go back to work.

---

## Theme

- Dark UI over satellite imagery. Light chrome fights busy terrain and reads amateur.
- Frosted panels, not solid cards: `rgba(18, 20, 18, 0.72)` + `backdrop-filter: blur(20px) saturate(1.2)` + a 1px white border at ~8% opacity.
- Scrim gradient across the top ~120px of the map so the floating bar always has contrast, regardless of what is underneath.
- One accent color only: blaze orange `#FF6B1A`. Restricted to active hunt state, primary button, focus rings. Nothing else.
- Every color and font as a CSS custom property in one file. Phase 7 should be editing one file, not hunting hex codes.

## Type

- Display (club name, big numbers): Fraunces. Variable serif, warm, slight woodcut quality.
- UI (everything else): Inter.
- Two typefaces total. No third.
- `font-variant-numeric: tabular-nums` on every time and duration, or rows wobble.

## Map and markers

- Shape encodes stand type: ladder, box, ground, climber.
- **Three states in V1, no more:**
  - Orange, pulsing: someone is in it right now
  - Gray: open
  - Red: overdue, checked in far too long with no checkout
- There is no "booked today" state. Nothing is scheduled in advance, so amber has no meaning.
- Wind dimming would be a fourth state and it is V2. Do not design for it here.
- Dark ring plus subtle halo on each marker so it separates from bright fields and dark timber alike.
- Member initials badge under occupied stands, readable without tapping. This is the 5am in the truck case.
- Level of detail by zoom:
  - below 13: boundary polygon and one club pin
  - 13 to 15: small status-colored dots, cluster if close
  - 15 and up: full icons, name labels, occupancy badges, gates and parking

## Guest markers

A guest stand glows the same as a member hunt. It is occupied either way, which is the thing a hunter needs to know at a glance. But it should be distinguishable, because who is in there changes how you approach it.

Options to pick from in Phase 7:

- Badge reads the guest name with a `(G)` suffix: `J. WALKER (G)`
- Dashed marker ring instead of solid, same orange
- Small secondary dot on the marker

Leaning toward the dashed ring plus `(G)` in the badge. Two signals, both readable at a glance, neither requires a legend lookup.

Panel copy for a guest stand: guest name first, host underneath as "Guest of Mike D." The host matters for accountability but the guest is who is physically there.

## The one button rule

The panel shows exactly one action, never a menu:

- Stand is open → **Check In**
- You are checked in here → **Check Out**
- Someone else is in it → no button, just status

Same position, different label. Members never see an option that does not apply to them.

## Motion

- Panel slide in, about 220ms, ease out.
- Active hunt pulse ring, 2 second cycle.
- Nothing else animates. If three things move, none of them read as important.
- Respect `prefers-reduced-motion`.

## States

- Loading skeletons, never spinners. Render boundary and gray marker placeholders immediately, fill in status when data lands.
- Empty state: nobody on the property. Should feel calm, not broken.
- Error state for a failed fetch that is not a blank screen.
- First run tooltip pointing at a stand, dismissed forever. This replaces the instructions section from the original sketch.

### Occupied stand state

Someone else checked in while the panel was open.

- Do not just say "occupied." Say who and since when: "Ray K. has been in Ridge Oak since 5:30."
- Refresh the map data at the same time the message appears, so what they see is now true.
- Suggest the nearest open stand.

### Guest partial failure state

Host stand is free, one guest stand got taken. Everything is rejected, so the message has to explain that clearly or it looks like a bug.

- Name which stand failed and why.
- Make it obvious the host check in did not go through either.
- Keep every name and phone number they already typed. Making them retype after a rejection is the fastest way to make an app feel hostile.

### No signal state

Check in happens in the timber before dawn. If the request fails, say so plainly and do not pretend it worked. A false "checked in" is worse than an error, because the whole point is knowing who is actually out there.

## Layout

- Map is full bleed. Everything else floats on top.
- Live hunter count top left, club name center, profile top right.
- Legend bottom left, collapsible, collapses to a single "?" circle on mobile.
- Panel slides from the right on desktop, bottom sheet on mobile.
- The map never unmounts.
- Guest section in the check in sheet is a toggle that reveals up to 2 rows of name, phone, and stand picker. Collapsed by default, since most sits have no guest.

## Copy and naming

- Call it a panel, never a sub page. Language shapes what gets built.
- Status strings: "Mike D. is hunting", "In since 5:42 am", "Nobody out right now".
- Live counter counts people, not sessions. Guests included. If it says 5, five people are on the property.
- Never say "booked" or "reserved" anywhere in the UI. Nothing is reserved. The words are "checked in" and "open".

## Unresolved

- Logo or wordmark
- Favicon
- Do stand photos belong in the panel
- What the logged out map looks like exactly
- Guest marker treatment: dashed ring, `(G)` badge, or both
- Should the panel show today's earlier sessions, or only what is live

## Parked for V2

Design work not worth doing until the feature exists.

- Schedule view and date pickers, if advance reservations ever ship
- Third and fourth marker states for capacity and wind
- Wind mismatch treatment. Open questions when it ships: dim the marker or add a small wind glyph, where today's forecast direction lives in the top bar, and whether a bad wind blocks check in or just warns.
- Harvest log entry screen
- Overdue alert presentation
- "Who forgot to check out" report styling
