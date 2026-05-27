# Amazon Advertising Optimization: Proven Best Practices

*Research compiled from 26+ practitioner sources, agency blogs, and Amazon's own documentation. Current as of April 2026.*

---

## 1. Bid Optimization: Proven Strategies

### The Core Bid Formulas

Three formulas dominate practitioner-recommended bid setting:

**Formula 1 — The Standard Target-ACOS Bid:**
```
Optimal Bid = (Average Order Value × Conversion Rate) × Target ACoS
```
*Example: $25 AOV × 10% CVR × 30% target ACoS = $0.75 bid*

**Formula 2 — Revenue Per Click Method:**
```
Keyword Bid = Revenue Per Click (RPC) × Target ACoS
```
*Example: $5 RPC × 30% target ACoS = $1.50 bid*
Sources: [AdLabs — 4 Essential Formulas](https://adlabs.app/amazon-ppc-bid-optimization-the-4-essential-formulas-for-optimizing-bids-hitting-your-target-acos/), [Ad Badger — ACoS Guide](https://www.adbadger.com/blog/amazon-ppc-education/acos-amazon/)

**Formula 3 — Bid Adjustment from Current Performance:**
```
New Bid = Current CPC × (Target ACoS / Current ACoS)
```
*Example: $1.20 CPC × (30% / 45%) = $0.80 new bid (bid reduction)*
Source: [AdLabs — Achieve ACOS Goals](https://adlabs.app/achieve-your-acos-goals-on-amazon-ppc-with-this-bidding-formula/)

**Formula 4 — Advanced Diagnostic:**
```
ACoS = (Avg CPC × (1 / CVR)) / Average Order Value
```
This breaks ACoS into controllable components to diagnose *which* driver is causing high ACoS (CPC too high, CVR too low, or AOV too low).
Source: [Ad Badger](https://www.adbadger.com/blog/amazon-ppc-education/acos-amazon/)

---

### The AdLabs 4-Category Bid Decision Framework

AdLabs identified four keyword performance states with specific rules for each:

| State | Condition | Action | Formula |
|-------|-----------|--------|---------|
| **High ACoS** | ACoS > target | Reduce bid | `Bid = RPC × Target ACoS` |
| **High Spend / No Sales** | Spend > target CPA, 0 conversions | Calculate safe bid | `(AOV ÷ clicks) × Target ACoS` |
| **Low ACoS** | ACoS ≥ 20% below target, has ≥1 sale | Increase bid +5–10% | Only if ACoS ≤ 24% when target is 30% |
| **Low Visibility** | Clicks < expected (aCTC threshold) | Increase bid +5% | aCTC = Clicks ÷ Orders; increase when clicks fall below threshold |

*Rule precedence: Low ACoS override > Low Visibility*
Source: [AdLabs — 4 Essential Formulas](https://adlabs.app/amazon-ppc-bid-optimization-the-4-essential-formulas-for-optimizing-bids-hitting-your-target-acos/)

---

### Bid Strategy Type Selection

| Strategy | Best For | Notes |
|----------|----------|-------|
| **Dynamic Bids — Down Only** | Cost control, beginners | Start here; Amazon reduces bids when conversion probability is low |
| **Dynamic Bids — Up and Down** | Scaling proven campaigns | Amazon adjusts up to 100% above bid; use after campaign has data |
| **Fixed Bids** | Predictability testing | No Amazon adjustment; full manual control |
| **Target ACoS (automated)** | Hands-off optimization | Amazon adjusts bids to hit your ACoS target; needs ~30 days of data |
| **Maximize Conversions** | New campaigns with budget | Spends entire daily budget optimizing for volume |

Source: [Signalytics — PPC Bid Strategy](https://signalytics.ai/amazon-ppc-bid-strategy/), [SellerMetrics — Bidding Strategies](https://sellermetrics.app/amazon-ppc-campaign-bidding-strategy/)

---

### Placement Bid Adjustments

Amazon's placement modifiers are a separate lever from keyword bids:
- **Top of Search**: Can be increased up to **900%** above base bid
- **Product Pages**: Separate adjustment; often warrants 0–50% increase
- **Rest of Search**: Default; rarely needs adjustment upward

*Practice: Run campaigns for 2+ weeks, then review ACoS by placement in the campaign manager. Increase placement modifier where ACoS is best; reduce or zero out where it's worst.*
Source: [Signalytics — PPC Bid Strategy](https://signalytics.ai/amazon-ppc-bid-strategy/)

---

### Key Warnings

- **Amazon's suggested bids inflate costs**: Suggested ranges run 20–30% above profitable levels. Always calculate your own bid using the formulas above.
- **Auction mechanics**: Amazon uses a **second-price auction** — you pay one cent above the next-highest bid, not your full bid. This means winning auctions is often cheaper than your bid suggests.
- **Bid increments**: Increase bids in +5–10% steps, not large jumps. Reduce high-ACOS keywords by 15–20% every few days rather than cutting sharply.
- **Minimum optimization frequency**: Weekly. Markets change, competitors adjust, and seasonality shifts bid efficiency continuously.

Sources: [Webycorp](https://webycorp.com/blog/amazon-ppc-optimization-bidding-strategies/), [Signalytics](https://signalytics.ai/amazon-ppc-bid-strategy/)

---

## 2. Budget Management Best Practices

### Allocation by Ad Type and Revenue Stage

| Revenue Stage | Sponsored Products | Sponsored Brands | Sponsored Display |
|--------------|-------------------|-------------------|-------------------|
| **< $1M/year** | 95% | 5% | — |
| **$1–5M/year** | 85% | 14% (video focus) | 1% (retargeting) |
| **> $5M/year** | 80% | 10–15% | Remainder |

Source: [Helium 10 — PPC Budgeting](https://www.helium10.com/blog/selling-on-amazon/amazon-ppc-budgeting-challenges-are-sinking-sales-heres-how-to-stay-afloat/)

Alternative framework (Emplicit):
- **Sponsored Products: 60–75%** — highest ROI, always prioritize
- **Sponsored Brands: 15–25%** — brand building and cross-sell
- **Sponsored Display: 5–10%** — retargeting and category reach
- **Testing Reserve: 10–20%** — A/B testing and new campaign discovery

Source: [Emplicit — Budget Allocation](https://emplicit.co/amazon-ppc-budget-how-to-allocate-funds/)

---

### Manual vs. Auto Campaign Split

- **Auto campaigns: 20%** of total budget — used strictly for discovery
- **Manual campaigns: 80%** — where optimization and scale happen

Gradually shift budget from auto to manual as high-performing keywords are harvested. Auto campaigns become less necessary as your exact/phrase match campaigns mature.
Source: [Emplicit](https://emplicit.co/amazon-ppc-budget-how-to-allocate-funds/)

---

### Performance-Tier Budget Framework

For allocating across campaigns within a portfolio:
- **40–50%** → campaigns with the highest proven conversion rates (winners)
- **30–40%** → campaigns showing growth potential (testing/scaling)
- **10–20%** → discovery and experimental campaigns

Source: [Emplicit — Budget Allocation](https://emplicit.co/amazon-ppc-budget-how-to-allocate-funds/)

---

### Portfolio-Level Budget Strategy (Amazon's Recommended Approach)

Amazon recommends setting **high individual campaign budgets** but capping spend at the **portfolio level**. This allows top-performing campaigns to spend freely without hitting artificial campaign caps, while the portfolio cap protects total spend.

```
Set campaign budgets = 2–3x your expected daily spend per campaign
Set portfolio cap = your actual total daily/monthly budget ceiling
```

Source: [Amazon Ads — Sponsored Products Budget Best Practices](https://advertising.amazon.com/library/guides/sponsored-products-budget-best-practices)

---

### Amazon's 2025/2026 Budget Rule Changes

Amazon's new dynamic budget rules can **extend daily caps by up to 25%** when it detects high purchase intent. Additionally, Amazon can now redistribute funds across campaigns it believes will perform better.

*Implication for automated systems: do not assume daily budget = hard cap. Monitor actual spend vs. budget for overage patterns.*

Source: [SellerMetrics — Amazon's New Budget Rules](https://sellermetrics.app/amazon-2025-budget-rules-update/)

---

### Budget Timing Rules

- **Daily budget calculation**: `Daily Budget = Monthly Budget ÷ 30`, then set limits at 110–120% to allow flexibility on high-traffic days
- **Review cadence**: Weekly (not daily — too reactive; not monthly — too slow)
- **Starting daily budgets by type**: Sponsored Products $10–50, Sponsored Brands $50–100, Sponsored Display $50–150
- **Seasonal pre-loading**: Increase budgets 50–100% before major events (Prime Day, Q4)

Sources: [Emplicit](https://emplicit.co/amazon-ppc-budget-how-to-allocate-funds/), [Canopy Management — ACoS & TACoS](https://canopymanagement.com/ultimate-guide-to-acos-and-tacos/)

---

## 3. Campaign Structure Best Practices

### The 4 Structure Methods (Ranked by Catalog Size)

| Method | Configuration | Best For | Key Limitation |
|--------|--------------|----------|----------------|
| **Multi-Product Ad Groups** | 1 Campaign → Multi Ad Groups → Multi Products | 5,000+ SKUs | Cannot isolate ASIN-level performance |
| **SPAGs** (Single Product Ad Groups) | 1 Campaign → Multi Ad Groups → 1 Product each | 500–5,000 SKUs | Placement data aggregates across products |
| **Single Product Campaigns** | 1 Campaign → 1 Ad Group → 1 Product → Multi Keywords | 1–500 SKUs | High campaign volume |
| **Single Keyword Campaigns (SKAGs)** | 1 Campaign → 1 Ad Group → 1 Product → 1 Keyword | VIP keywords only ($500+/month revenue) | Unmanageable at scale |

*Recommendation: Start with SPAGs for most catalogs. Graduate high-value keywords to SKAGs only when they justify the management overhead.*
Source: [AdLabs — Best Campaign Structure](https://adlabs.app/the-best-amazon-ppc-campaign-structure/)

---

### The RACI Budget Allocation Model

Eva Guru recommends this campaign budget allocation by responsibility tier:

| Campaign Type | Budget Share | Monitoring Cadence |
|--------------|-------------|-------------------|
| Exact Match Brand Defense | 25–30% | Daily |
| High-Converting Product Campaigns | 35–40% | Daily |
| Automatic Discovery Campaigns | 15–20% | Weekly |
| Competitor Analysis Campaigns | 10–15% | Weekly |
| Seasonal Testing Campaigns | 5–10% | Event-triggered |
| Long-Term Brand Building | 5–10% | Quarterly |

Source: [Eva Guru — Campaign Structure](https://eva.guru/blog/amazon-ppc-campaign-structure-professional-architecture/)

---

### The Waterfall Keyword Migration System

Keywords move through four performance-validated stages:

```
[Auto/Broad Discovery] 
       ↓ (≥20 clicks + ≥5% CVR)
[Phrase Match Validation]
       ↓ (≥50 clicks + ≥8% CVR)
[Exact Match Precision Targeting]
       ↓ (≥$500/month keyword revenue)
[SKAG Optimization — dedicated campaign]
```

**Critical rule**: When a keyword graduates, immediately add it as a **negative exact match** in the source campaign to prevent budget leakage (paying for the same term in two places at different bid levels).

Sources: [Eva Guru — Campaign Structure](https://eva.guru/blog/amazon-ppc-campaign-structure-professional-architecture/), [Pilothouse — Keyword Harvesting Guide](https://www.pilothouse.co/post/amazon-keyword-harvesting-the-complete-guide-to-search-term-graduation)

---

### Match Type Segmentation Strategy

Run separate campaigns by match type rather than mixing them:
- **Broad/Auto campaigns** → Discovery and keyword harvesting
- **Phrase campaigns** → Validation layer
- **Exact campaigns** → Scaling proven performers

Use **negative keywords** to prevent overlap. When a term graduates to exact, add it as a negative in the phrase and broad campaigns covering it.
Source: [AdLabs — Best Campaign Structure](https://adlabs.app/the-best-amazon-ppc-campaign-structure/)

---

### Optimal Bid Formula with Competitive Multipliers

Eva Guru's framework adds competitive and seasonal multipliers:
```
Optimal Bid = (AOV × CVR × Profit Margin) ÷ Target ACoS
```
Then apply multipliers:
- **Competitive intensity**: 0.8× (low competition) to 1.8× (high competition)
- **Seasonal adjustment**: 0.6× (off-season) to 2.0× (peak season)

Source: [Eva Guru — Campaign Structure](https://eva.guru/blog/amazon-ppc-campaign-structure-professional-architecture/)

---

## 4. Negative Keyword Strategy

### Click-Based Thresholds (Variable by Product)

There is **no universal click threshold**. The correct formula is:

```
Click Tolerance = Max Cost Per Acquisition ÷ Average CPC
```
*Example: $10 max CPA ÷ $0.25 avg CPC = 40 clicks before negating*

Or use the CVR-based rule:
```
Negate after: 2–3 × (clicks needed for 1 conversion based on your CVR)
```

| Product Type | Typical CVR | Negate After (Clicks, No Sales) |
|-------------|-------------|--------------------------------|
| High-CVR home improvement (15% CVR) | 1 conversion per ~7 clicks | 15–25 clicks |
| Mid-ticket $30–100 product | ~10% CVR | 20–30 clicks |
| Apparel (low CVR ~5%) | 1 conversion per ~20 clicks | 50–60 clicks |
| High-ticket $100+ items | Longer buying cycle | 40–50 clicks |

Sources: [KwickMetrics — Negation Thresholds](https://www.kwickmetrics.com/blog/find-the-threshold-for-keyword-negation-in-amazon-ads), [Amazon Growth Lab](https://www.amazongrowthlab.com/blogs/amazon-negative-keywords-ppc-strategy)

---

### Spend-Based Trigger

Regardless of click count, negate any search term that has consumed **$25–50 in spend with zero sales**. This is the most urgent signal of wasted budget.
Source: [Canopy Management — Negative Keywords](https://canopymanagement.com/build-a-negative-keyword-strategy-that-protects-your-amazon-ad-budget/)

---

### ACOS-Based Trigger

Negate (or aggressively bid-reduce) terms where:
- ACoS exceeds **2–3× your target** even after bid adjustments
- Terms that consistently burn budget without improving organically

---

### Account Health Benchmark

High-performing Amazon PPC accounts have **3–5× more negative keywords than targeted keywords**. If your negative keyword list is smaller than your positive list, the account is under-optimized.

---

### Match Type Protocol

1. **Start with Negative Exact Match** — blocks the specific term only
2. **Escalate to Negative Phrase** only after the pattern repeats across multiple Search Term Report cycles
3. Avoid **Negative Broad** unless you're certain the root word never converts (e.g., "free", "DIY", "tutorial")

---

### Review Cadence

- **Weekly**: Pull the Search Term Report; review all new clicks from the past 7 days against your thresholds
- **Monthly**: Audit phrase-level patterns to decide on phrase negatives
- **Quarterly**: Review and prune stale negatives that may now be blocking valid terms

Source: [Headline MA — Negative Keywords](https://www.headlinema.com/blog/amazon-negative-keywords), [KwickMetrics](https://www.kwickmetrics.com/blog/find-the-threshold-for-keyword-negation-in-amazon-ads)

---

## 5. Keyword Harvesting and Expansion

### The Auto-to-Manual Pipeline

```
Step 1: Launch Auto campaign (all 4 targeting types: close, loose, substitutes, complements)
Step 2: Wait minimum 14 days (Amazon needs data to learn)
Step 3: Pull Search Term Report
Step 4: Identify terms meeting graduation criteria (see below)
Step 5: Add qualified terms as Exact Match in manual campaign
Step 6: Immediately add the graduated term as Negative Exact in the Auto campaign
Step 7: Expand lookback window weekly: 14 days → 21 → 30 → stable 60-day routine
```

### Graduation Criteria (Eva Guru Waterfall)

| Stage Gate | Minimum Requirement |
|-----------|-------------------|
| Auto → Phrase (Validation) | ≥20 clicks AND ≥5% CVR |
| Phrase → Exact (Precision) | ≥50 clicks AND ≥8% CVR |
| Exact → SKAG (Dedicated Campaign) | ≥$500/month revenue from keyword |

Source: [Eva Guru — Campaign Structure](https://eva.guru/blog/amazon-ppc-campaign-structure-professional-architecture/)

### Alternative Criteria (Seller Sprite 3-Phase Model)

Cut underperformers at **11+ clicks with zero sales** (reduce bids; don't immediately add to manual).
Source: [Seller Sprite — Launch Strategy](https://www.sellersprite.com/en/blog/amazon-ppc-launch-strategy)

### Perpetua's Automated Threshold

Perpetua's algorithm auto-harvests at **2 conversions**. After ~380 keywords per ad group, the threshold rises to 3 conversions; at 500+ keywords, it rises to 7.
Source: [Perpetua Help Center](https://help.perpetua.io/en/articles/5734740-keyword-harvesting-in-perpetua)

### Key Rules for Harvesting

- **Exact match first**: Graduate to exact, not phrase. You want maximum control on proven terms.
- **Product price matters**: A $90 product may justify graduating a term at 5 ROAS; a $15 product needs 5–6 ROAS. Set thresholds relative to margins, not universally.
- **Never harvest without negating**: Leakage (auto campaign still running the graduated keyword at an unoptimized bid) is one of the most common wasted-spend sources.
- **Broad match → Phrase → Exact flow** for terms you want to expand before locking down.

Sources: [Pilothouse — Complete Harvesting Guide](https://www.pilothouse.co/post/amazon-keyword-harvesting-the-complete-guide-to-search-term-graduation), [Algofy — Keyword Harvesting](https://www.algofy.com/post/amazon-keyword-harvesting-how-to-find-hidden-search-terms-that-10x-your-organic-rankings)

---

## 6. Performance Measurement KPIs

### Foundational Formulas

| Metric | Formula | What It Tells You |
|--------|---------|------------------|
| **ACoS** | (Ad Spend / Ad Sales) × 100 | Campaign-level advertising efficiency |
| **TACoS** | (Ad Spend / Total Revenue) × 100 | Business-level advertising dependency |
| **ROAS** | Ad Sales / Ad Spend | Revenue per dollar spent (inverse of ACoS) |
| **Break-Even ACoS** | (Unit Margin / Selling Price) × 100 | Maximum ACoS before advertising loses money |
| **CTR** | (Clicks / Impressions) × 100 | Ad relevance and targeting accuracy |
| **CVR** | (Orders / Clicks) × 100 | Listing effectiveness at converting traffic |

**Conversion reference**: 20% ACoS = 5× ROAS | 25% ACoS = 4× ROAS | 33% ACoS = 3× ROAS
Sources: [PPC Ninja — KPIs](https://www.ppcninja.com/blog/amazon-ppc-kpis.html), [Canopy Management — ACoS & TACoS](https://canopymanagement.com/ultimate-guide-to-acos-and-tacos/)

---

### 2025/2026 Platform-Wide Benchmarks

| Metric | Value |
|--------|-------|
| Average CPC | $0.99–$1.04 (seasonal range: $0.82–$1.14) |
| Average Conversion Rate | 9.96–10.33% |
| Platform Average ACoS | 25–30% |
| Excellent ACoS (established product) | 15–20% |
| CPC growth trend | +15–30% in some categories YoY |

Source: [Canopy Management](https://canopymanagement.com/ultimate-guide-to-acos-and-tacos/), [SalesDuo Benchmarks](https://salesduo.com/blog/amazon-advertising-benchmarks/)

---

### Category-Specific ACoS Benchmarks

| Category | Typical ACoS Range | Notes |
|----------|-------------------|-------|
| **Electronics** | 13–21% | Narrowest margins; tightest targeting needed |
| **Beauty & Personal Care** | 18–28% | Higher acceptable for new launches; repeat purchase value |
| **Home & Kitchen** | 15–27% | Spikes to 30% during Q4 gifting season |
| **Pet Supplies** | 20–32% | High variability; first-purchase ACoS accepted if subscription follows |
| **Clothing, Shoes & Jewelry** | 22–38% | Widest range; segment by collection/season |

Source: [Xnurta — ACoS Benchmarks by Industry](https://www.xnurta.com/blog/amazon-acos-benchmarks-per-industry)

---

### ACoS Benchmarks by Ad Type

| Ad Type | CTR | CVR | ACoS |
|---------|-----|-----|------|
| Sponsored Products | 0.3–0.7% | 10–18% | 15–25% |
| Sponsored Brands | 0.4–0.9% | 8–12% | 20–35% |
| Sponsored Display | 0.2–0.5% | 5–10% | 25–40% |

Source: [SalesDuo Benchmarks](https://salesduo.com/blog/amazon-advertising-benchmarks/)

---

### ACoS / TACoS Targets by Product Lifecycle Stage

| Stage | Duration | Target ACoS | Target TACoS | Strategic Goal |
|-------|----------|-------------|--------------|---------------|
| **Launch** | 0–3 months | 40–50% | 15–25% | Build velocity, reviews, ranking |
| **Growth** | 3–12 months | 25–35% | 10–15% | Improve organic ranking, reduce PPC dependency |
| **Maturity** | 12+ months | 15–25% | 5–10% | Maximize profitability |
| **Liquidation** | — | Very high | — | Clear inventory |
| **Aggressive Growth Mode** | — | At/above breakeven | — | Reinvest all profit into ranking |

Source: [Canopy Management — ACoS & TACoS](https://canopymanagement.com/ultimate-guide-to-acos-and-tacos/), [PPC Ninja](https://www.ppcninja.com/blog/amazon-ppc-kpis.html)

---

### Key Metric Interpretation Rules

- **Low CTR** (< 0.3%): Wrong audience or weak creative → fix targeting or main image
- **High CTR, Low CVR**: Ad is attracting clicks but listing doesn't convert → fix listing, pricing, reviews
- **High ACoS, adequate CVR**: CPC is too high relative to order value → reduce bids
- **Improving TACoS with stable or rising ACoS**: Organic is growing — this is the success signal
- **PPC > 40% of total sales**: Concerning dependency; invest in organic rank improvement
- **PPC > 60% of total sales**: Organic decline indicator — diagnose immediately

Source: [PPC Ninja — KPIs](https://www.ppcninja.com/blog/amazon-ppc-kpis.html)

---

## 7. Dayparting and Time-Based Optimization

### Does It Work?

**Yes, with important caveats.** Practitioners report:
- 30–40% ACoS reduction when applied with a data-first approach
- Conversion rates can swing **300%** between peak and off-peak hours
- An average **51% increase in profits** from optimized hourly scheduling (self-reported by automation vendors)
- Evening hours (6 PM–12 AM local time) and weekends show the highest engagement for most consumer products

Sources: [Atom11 — Dayparting Guide](https://www.atom11.co/blog/amazon-ppc-dayparting-guide), [Eva Guru — Dayparting AI Strategies](https://eva.guru/blog/amazon-ppc-dayparting-ai-strategies/)

---

### Platform Limitations (Critical to Know)

- **Amazon's native "Schedule Rules" only allows bid UP** — you cannot bid DOWN using native tools
- True hourly bid reduction requires **third-party tools** (Perpetua, Intentwise, AdBrew, etc.)
- Manual dayparting rule setup is time-consuming and prone to overlap conflicts
- In 2025, Amazon launched **Amazon Drona** (select advertisers): real-time hourly bid and budget controls — the first native solution with full up/down capability

Sources: [Atom11](https://www.atom11.co/blog/amazon-ppc-dayparting-guide), [Intentwise — Dayparting](https://www.intentwise.com/blog/amazon-ppc-platform/dayparting-strategies/), [AdBrew — Dayparting](https://adbrew.io/blog/dayparting-for-amazon-ppc)

---

### When Dayparting Works vs. Backfires

| Works Well | Backfires |
|-----------|-----------|
| Campaigns frequently hitting daily budget caps | Everyday essentials (demand is uniform across hours) |
| Products with identifiable peak audiences (e.g., professionals, parents) | Ultra-low budgets (splitting thin budget creates gaps) |
| Seasonal events with concentrated demand windows | Flash sale promotions (need max visibility around clock) |
| High-ticket items with deliberate consideration cycles | Products with irregular/unpredictable conversion patterns |

Source: [Atom11 — Dayparting Guide](https://www.atom11.co/blog/amazon-ppc-dayparting-guide), [Jungle Scout — Dayparting](https://www.junglescout.com/resources/articles/amazon-ppc-dayparting/)

---

### Practitioner Implementation Recommendations

1. **Reduce bids during off-hours, don't pause completely** — pausing eliminates long-tail conversions that occur at unexpected times
2. **Segment by campaign type**: Brand, competitor, and category campaigns have different peak windows — use campaign-specific rules
3. **Review biweekly**: Shopping behaviors shift seasonally; weekly is too frequent, monthly too slow
4. **Start with broad windows** (morning/afternoon/evening buckets) before granular hour-by-hour rules
5. **Don't over-optimize**: Aggressive cutting creates blind spots in performance data

Sources: [Atom11](https://www.atom11.co/blog/amazon-ppc-dayparting-guide), [Jungle Scout](https://www.junglescout.com/resources/articles/amazon-ppc-dayparting/)

---

## 8. New Product Launch PPC Strategy

### The Seller Sprite 3-Phase Framework

The most widely cited structured launch approach:

**Phase 1: Rank and Reviews**
- **Duration**: Until reaching 10–20 reviews (more for competitive categories)
- **Target ACoS**: 2× your break-even ACoS *(e.g., if break-even is 30%, target 60%)*
- **Campaign mix**: Exact + Phrase + Broad match manual, product targeting (competitors and complementary), misspelling variations
- **Bid strategy**: Fixed bids or conservative Dynamic Down-Only
- **Posture**: "An investment phase — you are buying learning, ranking, and review momentum"

**Phase 2: Transition to Break-Even**
- **Duration**: ~30 days
- **Target ACoS**: 1.5× break-even *(e.g., 45%)*
- **Campaign additions**: Layer in Auto campaigns (now that Amazon has conversion data, auto performs better), optional category targeting
- **Optimization**: Begin controlled bid adjustments without cutting future winners prematurely

**Phase 3: Break-Even and Scale**
- **Duration**: Ongoing
- **Target ACoS**: Equal to break-even *(e.g., 30%)*
- **Campaign expansion**: Catch-all coverage campaigns, scale layers based on proven data
- **Focus**: Maintain growth while protecting profitability

Source: [Seller Sprite — PPC Launch Strategy](https://www.sellersprite.com/en/blog/amazon-ppc-launch-strategy)

---

### Launch-Specific Tactical Rules

- **Start optimizing**: 7–14 days after your first sale (not before — too little data)
- **Lookback window expansion**: Start at 14 days → expand to 21 → 30 → settle at a 60-day routine
- **Cut underperformers**: Any keyword with 11+ clicks and zero sales → reduce bid 15–20%
- **Inventory gate**: Push PPC hardest when SKU has 60+ days of stock; throttle back if stock drops below 30 days — getting ranking then stocking out destroys the investment
- **View as data acquisition**: The goal of launch PPC is keyword intelligence + sales history + review acceleration, not immediate profitability

Sources: [Seller Sprite](https://www.sellersprite.com/en/blog/amazon-ppc-launch-strategy), [Headline MA — Product Launch](https://www.headlinema.com/blog/product-launch-strategies)

---

### ASIN Targeting During Launch

- **Competitor ASIN targeting**: Run Sponsored Product campaigns targeting competitor ASINs. Ads show on their product pages AND on keywords they rank for — dual exposure.
- **Own product ASIN defense**: Set up Sponsored Brand or Sponsored Display targeting your own ASINs to capture customers who are browsing your pages.
- **Placement tip**: Use **Product Page placement** (rather than Top of Search) for competitor targeting to avoid cannibalizing your own keyword campaigns.

Source: [eComClips — PPC Ads Strategy 2025](https://ecomclips.com/blog/amazon-ppc-ads-strategy-2025-how-to-launch-your-new-product/)

---

### How Launch Strategy Differs from Established Products

| Dimension | Launch (Phase 1) | Established Product |
|-----------|-----------------|---------------------|
| Target ACoS | 2× break-even or more | At or below break-even |
| Primary goal | Velocity + data + reviews | Profitability + defense |
| Match type focus | Broad/auto for discovery | Exact for efficiency |
| Bidding mode | Aggressive / Fixed | Optimized Dynamic |
| Negative keyword cadence | Weekly | Weekly (larger list) |
| Campaign auto layer | Later (after data) | Optional discovery only |

---

## 9. Seasonal and Event-Based Optimization

### Prime Day Strategy

**Timeline**:
- **4+ weeks before**: Optimize listings, build review pipeline, prepare deal ASINs, run awareness Sponsored Brand campaigns
- **3–5 days before**: Begin scaling budgets in 20–30% increments to capture research-phase traffic
- **Day of**: Set daily budgets at **2–3× normal daily average** for deal ASINs; implement real-time midday monitoring

**The Two-Bucket Campaign Strategy**:
| Bucket | Approach |
|--------|----------|
| **Deal ASINs** (active promotions) | 2–3× budget, aggressive top-of-search bids, maximum visibility |
| **Non-deal ASINs** | Lean budgets or pause entirely; redirect spend to promoted products |

**Post-event**:
- Maintain campaigns for **2 weeks after Prime Day** — delayed approval orders and restock orders continue arriving
- Reallocate budget from underperformers to winners
- Activate Sponsored Display and DSP for retargeting customers who browsed but didn't convert during the event

Amazon's data: Display ads using cost controls saw a **2.6× uplift in ad-attributed sales per campaign** during Prime Day.

Sources: [Amazon Ads — Prime Day Advanced Strategies](https://advertising.amazon.com/library/guides/prime-day-guide-advanced-strategies), [Incrementum Digital — Prime Day](https://incrementumdigital.com/blog/advertising/amazon-prime-day-amazon-advertising-strategies/)

---

### Black Friday / Cyber Monday Strategy

**Timeline**:
- **4 weeks before**: Launch new campaigns — not less, because new campaigns need history to compete effectively. Clicks and conversions begin rising 2 weeks before the event.
- **By September 30**: Submit deals through Seller Central (Amazon's deal submission deadline)
- **During event**: Increase bids by **10–30%** on proven performers; increase budgets — campaigns that hit their daily cap before peak hours lose placement when competitors are still spending
- **Keyword rule**: Do NOT add new keywords in the final week — they lack the history to generate effective results during the event

**Post-event**:
- Do NOT make immediate changes after Cyber Monday
- Sales momentum continues through Christmas week — the shopping season doesn't end on December 1
- Document learnings and performance data for next year's planning

Source: [Adspert — Black Friday Strategy](https://www.adspert.net/amazon-black-friday-strategy-guide/)

---

### General Q4 / Seasonal Rules

- **Budget increase**: 50–100% above normal before major events
- **Keyword refresh**: Research seasonal terms (e.g., "gift for him", "stocking stuffer") 4–6 weeks before peak; add early enough for Amazon to gather data
- **Monitor daily** during peak periods (versus weekly during normal periods)
- **Shift budgets intra-day**: During events, move budget multiple times daily to follow profitable traffic
- **Avoid overbidding panic**: Compete aggressively on proven performers, not across-the-board increases

Sources: [Ad Badger — Peak Season Mistakes](https://www.adbadger.com/blog/common-amazon-ppc-mistakes-sellers-make-during-peak-season/), [Canopy Management — ACoS & TACoS](https://canopymanagement.com/ultimate-guide-to-acos-and-tacos/)

---

## 10. Common Mistakes and Pitfalls

### The 7 Most Damaging Mistakes (with Remedies)

**1. Set-It-and-Forget-It**
- **What happens**: Markets shift, competitors adjust, seasonality changes — campaigns become inefficient within days
- **Real data**: 40% of spend can flow to irrelevant terms without active management
- **Remedy**: Minimum weekly optimization cycle; automate alerts for ACOS spikes >50% above target

**2. No Negative Keywords (or Too Few)**
- **What happens**: Amazon matches ads to irrelevant queries; CTR drops → Amazon loses confidence in product relevance → organic rank suffers
- **Benchmark**: High-performing accounts have 3–5× more negative keywords than positive keywords
- **Remedy**: Pull Search Term Report weekly; apply spend-based ($25–50 without a sale) and click-based thresholds

**3. Poor Campaign Structure (Keyword Cannibalization)**
- **What happens**: Multiple campaigns compete for the same terms; costs inflate and attribution becomes meaningless
- **Remedy**: Separate by match type; use negative keyword bridges between campaigns; segment by product family

**4. ACoS Tunnel Vision (Ignoring TACoS)**
- **What happens**: Automated systems pause campaigns that are actually driving organic growth, destroying velocity
- **The trap**: A 45% ACoS campaign might be dropping TACoS from 20% to 12% by building organic rank — killing it destroys the halo effect
- **Remedy**: Use TACoS as the primary optimization target, not ACoS alone

**5. Over-Reliance on Auto Campaigns**
- **What happens**: Amazon's auto campaign optimizes for clicks, not conversions — conflicting objective
- **Remedy**: Use auto campaigns exclusively for discovery; harvest and graduate high performers weekly; manual campaigns drive all scale

**6. Not Defending Brand Keywords**
- **What happens**: Competitors fill the vacuum on high-intent, low-CPC branded searches
- **Cost**: Brand keywords are typically the highest-converting, lowest-ACOS keywords in any account
- **Remedy**: Dedicated brand defense campaigns with floor bids that never get paused; budget protected separately from category spend

**7. Launching Multiple Campaigns Simultaneously Without Data**
- **What happens**: Budget spreads thin across many campaigns; no single campaign accumulates enough data to optimize; all underperform
- **Remedy**: Launch 2–3 core campaigns per product (one auto, one manual exact, one manual broad/phrase), then scale after 14+ days of data

Sources: [SellerMetrics — 7 Mistakes](https://sellermetrics.app/seven-common-amazon-ad-mistakes/), [Canopy Management — 5 Mistakes](https://canopymanagement.com/5-top-amazon-ppc-mistakes/), [Ad Badger — 2025 Challenges](https://www.adbadger.com/blog/the-toughest-cases-and-common-mistakes-amazon-sellers-face-in-2025/)

---

### Mistakes Specific to Automated Optimization Systems

These pitfalls are especially relevant for any algorithmic bid management system:

| Mistake | Why It Matters for Automation | Fix |
|---------|------------------------------|-----|
| Optimizing solely on ACoS | Kills campaigns with positive organic halo effect | Optimize for TACoS; track keyword ranking alongside paid metrics |
| Pausing campaigns too aggressively | Destroys historical data; Amazon treats relaunched campaigns as new (loses performance history) | Reduce bids gradually; never kill campaigns with significant sales history |
| Flat bids across placements | Top-of-search and product-page have different conversion rates | Adjust placement multipliers separately from keyword bids |
| Harvesting without negating | Creates budget leakage (same term running in two campaigns at different bids) | Graduation and negation must be atomic operations |
| Ignoring listing quality signals | No amount of bid optimization overcomes a listing with poor images, low reviews, or thin copy | Pre-flight checks; pause PPC if listing falls below retail readiness threshold |
| Over-optimizing with daily data | Too-frequent bid changes create oscillation and prevent accurate measurement | Use 7–14 day data windows for bid decisions; optimize weekly not daily |

Sources: [SellerMetrics — 7 Mistakes](https://sellermetrics.app/seven-common-amazon-ad-mistakes/), [Optmyzr — Why PPC Campaigns Fail](https://www.optmyzr.com/blog/structure-amazon-ppc-campaigns-for-maximum-ROI/)

---

## Reference Summary

### Quick Decision Lookup

| Question | Answer | Source |
|----------|--------|--------|
| How do I calculate my starting bid? | `(AOV × CVR) × Target ACoS` | [AdLabs](https://adlabs.app/amazon-ppc-bid-optimization-the-4-essential-formulas-for-optimizing-bids-hitting-your-target-acos/) |
| When do I lower a bid? | ACOS > target, use formula: `Current CPC × (Target ACoS / Current ACoS)` | [AdLabs](https://adlabs.app/achieve-your-acos-goals-on-amazon-ppc-with-this-bidding-formula/) |
| When do I negate a keyword? | After `Max CPA ÷ Avg CPC` clicks with no sale, OR $25–50 spend with no sale | [KwickMetrics](https://www.kwickmetrics.com/blog/find-the-threshold-for-keyword-negation-in-amazon-ads) |
| When does a search term graduate to manual? | ≥20 clicks AND ≥5% CVR (phrase), then ≥50 clicks AND ≥8% CVR (exact) | [Eva Guru](https://eva.guru/blog/amazon-ppc-campaign-structure-professional-architecture/) |
| What's a good TACoS? | 5–15% for established products | [PPC Ninja](https://www.ppcninja.com/blog/amazon-ppc-kpis.html) |
| What's the target ACoS at launch? | 2× break-even (Phase 1), 1.5× (Phase 2), break-even (Phase 3) | [Seller Sprite](https://www.sellersprite.com/en/blog/amazon-ppc-launch-strategy) |
| How much should I increase budget for Prime Day? | 2–3× daily average for deal ASINs; start scaling 3–5 days before | [Amazon Ads](https://advertising.amazon.com/library/guides/prime-day-guide-advanced-strategies) |
| How do I split budget across ad types? | SP: 60–85%, SB: 5–25%, SD: 0–10% (scale with revenue size) | [Helium 10](https://www.helium10.com/blog/selling-on-amazon/amazon-ppc-budgeting-challenges-are-sinking-sales-heres-how-to-stay-afloat/) |

---

### All Sources

1. [AdLabs — 4 Essential Bid Optimization Formulas](https://adlabs.app/amazon-ppc-bid-optimization-the-4-essential-formulas-for-optimizing-bids-hitting-your-target-acos/)
2. [AdLabs — Achieve Your ACoS Goals](https://adlabs.app/achieve-your-acos-goals-on-amazon-ppc-with-this-bidding-formula/)
3. [AdLabs — Best Campaign Structure: 4 Methods](https://adlabs.app/the-best-amazon-ppc-campaign-structure/)
4. [Ad Badger — ACoS Amazon](https://www.adbadger.com/blog/amazon-ppc-education/acos-amazon/)
5. [Ad Badger — Peak Season PPC Mistakes](https://www.adbadger.com/blog/common-amazon-ppc-mistakes-sellers-make-during-peak-season/)
6. [Ad Badger — Toughest Cases 2025](https://www.adbadger.com/blog/the-toughest-cases-and-common-mistakes-amazon-sellers-face-in-2025/)
7. [Adspert — Amazon Black Friday Strategy](https://www.adspert.net/amazon-black-friday-strategy-guide/)
8. [Amazon Ads — Prime Day Advanced Strategies](https://advertising.amazon.com/library/guides/prime-day-guide-advanced-strategies)
9. [Amazon Ads — Sponsored Products Budget Best Practices](https://advertising.amazon.com/library/guides/sponsored-products-budget-best-practices)
10. [Atom11 — Dayparting Guide](https://www.atom11.co/blog/amazon-ppc-dayparting-guide)
11. [Canopy Management — ACoS and TACoS Guide](https://canopymanagement.com/ultimate-guide-to-acos-and-tacos/)
12. [Canopy Management — Negative Keywords](https://canopymanagement.com/build-a-negative-keyword-strategy-that-protects-your-amazon-ad-budget/)
13. [Canopy Management — 5 Campaign-Killing Mistakes](https://canopymanagement.com/5-top-amazon-ppc-mistakes/)
14. [eComClips — PPC Ads Strategy 2025](https://ecomclips.com/blog/amazon-ppc-ads-strategy-2025-how-to-launch-your-new-product/)
15. [Emplicit — Budget Allocation](https://emplicit.co/amazon-ppc-budget-how-to-allocate-funds/)
16. [Eva Guru — Campaign Structure Architecture](https://eva.guru/blog/amazon-ppc-campaign-structure-professional-architecture/)
17. [Eva Guru — Dayparting AI Strategies](https://eva.guru/blog/amazon-ppc-dayparting-ai-strategies/)
18. [Headline MA — Product Launch Strategies](https://www.headlinema.com/blog/product-launch-strategies)
19. [Headline MA — Negative Keywords](https://www.headlinema.com/blog/amazon-negative-keywords)
20. [Helium 10 — PPC Budgeting Challenges](https://www.helium10.com/blog/selling-on-amazon/amazon-ppc-budgeting-challenges-are-sinking-sales-heres-how-to-stay-afloat/)
21. [Incrementum Digital — Prime Day Strategy](https://incrementumdigital.com/blog/advertising/amazon-prime-day-amazon-advertising-strategies/)
22. [Intentwise — Dayparting](https://www.intentwise.com/blog/amazon-ppc-platform/dayparting-strategies/)
23. [Jungle Scout — Dayparting](https://www.junglescout.com/resources/articles/amazon-ppc-dayparting/)
24. [KwickMetrics — Keyword Negation Thresholds](https://www.kwickmetrics.com/blog/find-the-threshold-for-keyword-negation-in-amazon-ads)
25. [Mexperts Marketing — Keyword Harvesting 2025](https://mexpertsmarketing.com/amazon-keyword-harvesting-2025/)
26. [Optmyzr — Why PPC Campaigns Fail](https://www.optmyzr.com/blog/structure-amazon-ppc-campaigns-for-maximum-ROI/)
27. [Perpetua Help Center — Keyword Harvesting](https://help.perpetua.io/en/articles/5734740-keyword-harvesting-in-perpetua)
28. [Pilothouse Digital — Complete Keyword Harvesting Guide](https://www.pilothouse.co/post/amazon-keyword-harvesting-the-complete-guide-to-search-term-graduation)
29. [PPC Ninja — Amazon PPC KPIs](https://www.ppcninja.com/blog/amazon-ppc-kpis.html)
30. [SalesDuo — Amazon Advertising Benchmarks](https://salesduo.com/blog/amazon-advertising-benchmarks/)
31. [Seller Labs — PPC Strategies 2025](https://www.sellerlabs.com/blog/amazon-ppc-strategies-2025/)
32. [SellerMetrics — 7 Common Ad Mistakes](https://sellermetrics.app/seven-common-amazon-ad-mistakes/)
33. [SellerMetrics — Amazon's New Budget Rules](https://sellermetrics.app/amazon-2025-budget-rules-update/)
34. [Seller Sprite — PPC Launch Strategy in 3 Phases](https://www.sellersprite.com/en/blog/amazon-ppc-launch-strategy)
35. [Signalytics — PPC Bid Strategy](https://signalytics.ai/amazon-ppc-bid-strategy/)
36. [Xnurta — ACoS Benchmarks Per Industry](https://www.xnurta.com/blog/amazon-acos-benchmarks-per-industry)
