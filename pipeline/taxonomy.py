"""Event taxonomy: what each event type is, how to recognise it in a headline,
and which instruments to measure when backtesting it.

Instruments are Yahoo Finance symbols. FX baskets are kept USD-base (USD/xxx)
so that averaging is coherent; EURUSD/GBPUSD appear alone in their own basket.

Classifier patterns (case-insensitive regex, Python & JS compatible):
  all   - list of groups; every group must match somewhere in the text
  boost - optional patterns; each hit raises match confidence
  none  - any hit vetoes the type
"""

MARKETS = ["US Equities", "EU Equities", "Asia Equities", "Rates", "Forex", "Commodities", "Crypto", "Single Stocks"]

# Region of each instrument's primary session, used to align event timing.
# Federal Reserve H.10 noon (12:00 ET) buying rates. Verification showed Yahoo's daily
# FX bars correlate only ~0.45 with these on the same day (their "close" time is not
# documented), while H.10 has a known timestamp, so the backtest uses H.10 for FX.
FRED_FX = {
    "EURUSD=X": "DEXUSEU", "GBPUSD=X": "DEXUSUK", "AUDUSD=X": "DEXUSAL",
    "JPY=X": "DEXJPUS", "USDJPY=X": "DEXJPUS", "CNY=X": "DEXCHUS", "USDCNY=X": "DEXCHUS",
    "MXN=X": "DEXMXUS", "USDMXN=X": "DEXMXUS", "BRL=X": "DEXBZUS", "USDBRL=X": "DEXBZUS",
    "INR=X": "DEXINUS", "USDINR=X": "DEXINUS", "KRW=X": "DEXKOUS", "USDKRW=X": "DEXKOUS",
    "CHF=X": "DEXSZUS", "USDCHF=X": "DEXSZUS", "ZAR=X": "DEXSFUS", "USDZAR=X": "DEXSFUS",
    "CAD=X": "DEXCAUS", "USDCAD=X": "DEXCAUS",
}


def backtest_symbol(symbol: str) -> str:
    """Price series used for historical event studies (live tracking keeps Yahoo)."""
    return "FRED:" + FRED_FX[symbol] if symbol in FRED_FX else symbol


def region_of(symbol: str) -> str:
    if symbol.startswith("FRED:DEX"):
        return "fxnoon"
    asia = (".HK", ".T", ".KS", ".KQ", ".TW", ".SS", ".SZ", ".AX", ".NS", ".BO")
    eu = (".PA", ".DE", ".L", ".AS", ".MC", ".MI", ".CO", ".SW", ".ST", ".BR", ".OL", ".HE", ".IR", ".LS", ".VI")
    if symbol.endswith(asia) or symbol in ("^N225", "^HSI", "^KS11", "^TWII", "000001.SS", "^AXJO"):
        return "asia"
    if symbol.endswith(eu) or symbol in ("^STOXX50E", "^GDAXI", "^FTSE", "^FTMC", "^FCHI", "^STOXX"):
        return "eu"
    return "us"  # US equities, futures, FX, crypto: daily bars close at/after US close


# Applied to every type unless spec_ok=True: speculation, previews, explainers,
# retrospectives and questions are not "an event just happened".
GLOBAL_NONE_CORE = [
    r"\?",
    r"^(what|why|how|when|where|who|is|are|will|can|should|could|here's|here is|explainer|analysis|opinion|watch|live:?|podcast)\b",
    r"\b(could|might|would|should|may (cut|raise|hike|boost|impose|launch|approve|be|have|not|soon|need|face|delay)|likely|unlikely|possible|possibly|potential(ly)?|(is|are|was|were|seen|widely) expected to|expected to (raise|hike|cut|lower|hold|keep|announce|approve|reject|rise|fall|increase|decrease|reach|remain|launch|impose|sign)|set to|poised to|on track to|plans? to|seeks? to|aims? to)\b",
]
# Commentary words: fine around a data release or company news, but for policy and
# geopolitical types they mark speculation rather than an event.
GLOBAL_NONE_SPEC = [
    r"\b(case for|odds|bets?|betting|wagers?|pric(e|es|ed|ing) in|fears?|feared|risk of|risks|threat(en|ens|ened)?|weigh(s|ing)?|consider(s|ing)?|mull(s|ing)?|debat(e|es|ing)|calls? for|urges?|presses|pressures?|pushes for|pointing toward|path|ahead of|preview|week ahead|what to (know|expect|watch)|lessons|history shows|since the|after every|years? after|anniversary|roundup|recap|timeline|explained)\b",
]
GLOBAL_NONE = GLOBAL_NONE_CORE + GLOBAL_NONE_SPEC


# Types whose historical catalog was partly chosen *because* the stock moved a lot
# (famous earnings shocks, notable FDA decisions). Their direction is informative;
# their magnitude is overstated.
OUTCOME_SELECTED = {"megacap_earnings_beat", "megacap_earnings_miss", "fda_approval", "fda_trial_failure",
                    "ai_frontier_launch", "major_recall"}
BIAS_NOTE = "Catalog partly selected by price reaction: direction is informative, magnitude is overstated."


def T(id, label, cat, geo, all_, impacts, boost=(), none=(), company_specific=False,
      default_tickers=(), benchmark=None, source="catalog", note="", spec_ok=False, commentary_ok=False):
    if id in OUTCOME_SELECTED:
        note = (note + " " + BIAS_NOTE).strip()
    return dict(id=id, label=label, cat=cat, geo=geo, source=source, note=note, outcome_selected=id in OUTCOME_SELECTED,
                patterns=dict(all=list(all_), boost=list(boost),
                              none=list(none) + ([] if spec_ok else GLOBAL_NONE_CORE if commentary_ok else GLOBAL_NONE)),
                impacts=impacts, company_specific=company_specific,
                default_tickers=list(default_tickers), benchmark=benchmark)


def I(mkt, name, instruments, note=""):
    return dict(mkt=mkt, name=name, instruments=instruments, note=note)


# Non-US places/demonyms: US-macro types (CPI, payrolls) are vetoed when the headline is about
# another economy ("Ukraine's economy slips into contraction as inflation hits 10%").
NON_US = (r"\b(uk|britain|british|england|euro ?(zone|area)|euro|europe(an)?|china|chinese|japan(ese)?|india(n)?|"
          r"german(y)?|canad(a|ian)|australia(n)?|new zealand|france|french|ital(y|ian)|spain|spanish|swed(en|ish)|"
          r"norw(ay|egian)|swiss|switzerland|poland|polish|russia(n)?|ukrain(e|ian)|turk(ey|ish|iye)|argentin(a|e|ian)|"
          r"brazil(ian)?|mexic(o|an)|colombia(n)?|chile(an)?|peru(vian)?|venezuela(n)?|south africa(n)?|nigeria(n)?|"
          r"egypt(ian)?|kenya(n)?|ghana(ian)?|pakistan(i)?|sri lanka(n)?|bangladesh(i)?|indonesia(n)?|vietnam(ese)?|"
          r"philippines?|filipino|thai(land)?|malaysia(n)?|singapore(an)?|korea(n)?|taiwan(ese)?|hong kong|iran(ian)?|"
          r"israel(i)?|saudi|uae|emirati|qatar|zimbabwe(an)?|ethiopia(n)?|lebanon|lebanese|belarus|kazakhstan|"
          r"scotland|scottish|ireland|irish|netherlands|dutch|belgi(um|an)|austria(n)?|denmark|danish|finland|finnish|"
          r"greece|greek|portugal|portuguese|czech|hungar(y|ian)|romania(n)?|uruguay|ecuador|bolivia|kosovo|serbia(n)?|"
          r"croatia(n)?|bosnia(n)?|albania(n)?|bulgaria(n)?|slovakia|slovenia|lithuania|latvia|estonia|armenia(n)?|"
          r"azerbaijan|uzbekistan|mongolia(n)?|nepal(ese)?|myanmar|cambodia|morocco|moroccan|tunisia|algeria|angola|"
          r"zambia|uganda|tanzania|rwanda|senegal|cameroon|jamaica|paraguay|iceland|cyprus|malta|luxembourg|"
          r"jordan(ian)?|iraq(i)?|kuwait(i)?|oman|bahrain)\b")

# CPI subject: the index itself, or "inflation" used as a data point (inflation rose/eased/rate/data),
# not "inflation concerns" or "inflation squeeze". Live false positives (7-9 Nov 2026) all used
# bare "inflation" in commentary.
CPI_SUBJ = (r"\b(cpi|consumer price(s| index)?|inflation (rate|data|report|figures?|reading|print|numbers?|rose|rises|"
            r"jumped|jumps|climbed|climbs|accelerat\w*|cooled|cools|eased|eases|slowed|slows|fell|falls|picks? up|"
            r"quicken\w*|hit|hits|tops|topped|surged?|surges|ticks? (up|down)|unexpectedly))\b")
CPI_COMMENTARY = (r"\b(concerns?|fears?|worries|worried|squeeze|risks?|holds? the key|ahead of|awaits?|eyes|preview|"
                  r"week ahead|due (out|on)|set to|watch(es|ing)?)\b")

FED = r"\b(fed|federal reserve|fomc|powell)\b"
ECB = r"\b(ecb|european central bank|lagarde)\b"
HIKE = r"\b(hikes?|hiked|hiking|raises?|raised|raising|increases?|lifts?)\b"
CUT = r"\b(cuts?|cutting|lowers?|lowered|reduces?|reduced|slashes?|eases?)\b"
RATE = r"\b(rates?|basis points?|bps?|bp)\b"
NEG_ACTION = r"\b(holds?|holding|keeps?|leaves?|unchanged|pause[sd]?|steady|odds|bets?|expectations?|could|might|weighs?|considers?|calls? for|urges?|debate|minutes)\b"

USD_FX = {"DX-Y.NYB": "Dollar index", "JPY=X": "USD/JPY", "MXN=X": "USD/MXN", "BRL=X": "USD/BRL", "INR=X": "USD/INR"}
US_GROWTH = {"QQQ": "Nasdaq 100", "XLK": "Tech", "XLRE": "Real estate", "XLY": "Cons. discretionary", "IWM": "Small caps", "SPY": "S&P 500"}

EVENT_TYPES = [
    # ── ECONOMIC ─────────────────────────────────────────────────────────────
    T("fed_hike", "Fed interest rate hike", "Economic", "United States",
      [FED, HIKE, RATE], none=[NEG_ACTION, r"\bcut", r"\bhike (odds|bets)"],
      boost=[r"\b(25|50|75)\s?(bp|bps|basis)", r"\bannounce|decision|statement"],
      source="fred",
      impacts=[I("US Equities", "Rate-sensitive equities", US_GROWTH),
               I("Rates", "Treasuries", {"TLT": "20y+ Treasuries", "IEF": "7-10y Treasuries", "SHY": "1-3y Treasuries"}),
               I("Forex", "USD vs majors & EM", USD_FX),
               I("Commodities", "Gold", {"GC=F": "Gold"}),
               I("Crypto", "Bitcoin", {"BTC-USD": "Bitcoin"})]),
    T("fed_cut", "Fed interest rate cut", "Economic", "United States",
      [FED, CUT, RATE], none=[NEG_ACTION, r"\bhike", r"\bjob cuts?\b"],
      boost=[r"\b(25|50|75)\s?(bp|bps|basis)", r"\bannounce|decision|statement|emergency"],
      source="fred",
      impacts=[I("US Equities", "Rate-sensitive equities", US_GROWTH),
               I("Rates", "Treasuries", {"TLT": "20y+ Treasuries", "IEF": "7-10y Treasuries", "SHY": "1-3y Treasuries"}),
               I("Forex", "USD vs majors & EM", USD_FX),
               I("Commodities", "Gold", {"GC=F": "Gold"}),
               I("Crypto", "Bitcoin", {"BTC-USD": "Bitcoin"})]),
    T("ecb_hike", "ECB interest rate hike", "Economic", "European Union",
      [ECB, HIKE, RATE], none=[NEG_ACTION, r"\bcut"], source="fred",
      impacts=[I("EU Equities", "European banks", {"BNP.PA": "BNP Paribas", "DBK.DE": "Deutsche Bank", "SAN.MC": "Santander", "INGA.AS": "ING", "UCG.MI": "UniCredit"}),
               I("EU Equities", "Rate-sensitive (RE & utilities)", {"VNA.DE": "Vonovia", "RWE.DE": "RWE", "EOAN.DE": "E.ON", "^STOXX50E": "Euro Stoxx 50"}),
               I("Forex", "EUR/USD", {"EURUSD=X": "EUR/USD"})]),
    T("ecb_cut", "ECB interest rate cut", "Economic", "European Union",
      [ECB, CUT, RATE], none=[NEG_ACTION, r"\bhike"], source="fred",
      impacts=[I("EU Equities", "European banks", {"BNP.PA": "BNP Paribas", "DBK.DE": "Deutsche Bank", "SAN.MC": "Santander", "INGA.AS": "ING", "UCG.MI": "UniCredit"}),
               I("EU Equities", "Rate-sensitive (RE & utilities)", {"VNA.DE": "Vonovia", "RWE.DE": "RWE", "EOAN.DE": "E.ON", "^STOXX50E": "Euro Stoxx 50"}),
               I("Forex", "EUR/USD", {"EURUSD=X": "EUR/USD"})]),
    T("yield_curve_inversion", "US yield curve inverts (2y > 10y)", "Economic", "United States",
      [r"\b(yield curve|2s10s|2-year|two-year)\b", r"\binver(t|ts|ted|sion)\b"], source="fred",
      note="Few occurrences since 2000; treat statistics as anecdotal.",
      impacts=[I("US Equities", "Banks & cyclicals", {"XLF": "Financials", "KBE": "Bank ETF", "XLI": "Industrials", "SPY": "S&P 500"})]),
    T("us_cpi_hot", "US CPI above forecast", "Economic", "United States",
      [CPI_SUBJ, r"\b(hotter|higher|above|beats?|tops?|exceeds?|accelerat\w*|surges?|jumps?|rises? more)\b"],
      none=[r"\b(officials?|beat inflation|fight\w* inflation|tame inflation|pointing|target)\b", CPI_COMMENTARY, r"\b(cooler|slows?|below|eases?|softer|misses?)\b", NON_US],
      boost=[r"\b(expect|forecast|estimate|consensus)", r"\bcore\b", r"\b(us|u\.s\.)\b"],
      commentary_ok=True,
      impacts=[I("US Equities", "Broad & growth", {"SPY": "S&P 500", "QQQ": "Nasdaq 100", "IWM": "Small caps", "XHB": "Homebuilders"}),
               I("Rates", "Treasuries", {"TLT": "20y+ Treasuries", "IEF": "7-10y Treasuries", "SHY": "1-3y Treasuries"}),
               I("Forex", "USD", {"DX-Y.NYB": "Dollar index", "JPY=X": "USD/JPY"}),
               I("Commodities", "Gold", {"GC=F": "Gold"}),
               I("Crypto", "Bitcoin", {"BTC-USD": "Bitcoin"})]),
    T("us_cpi_cool", "US CPI below forecast", "Economic", "United States",
      [CPI_SUBJ, r"\b(cool|cooler|cools|cooled|slows?|slowed|below|eases?|eased|softer|misses?|decelerat\w*)\b"],
      none=[r"\b(officials?|beat inflation|fight\w* inflation|tame inflation|pointing|target)\b", CPI_COMMENTARY, r"\b(hotter|above|beats?|accelerat\w*)\b", NON_US],
      boost=[r"\b(expect|forecast|estimate|consensus)", r"\bcore\b", r"\b(us|u\.s\.)\b"],
      commentary_ok=True,
      impacts=[I("US Equities", "Broad & growth", {"SPY": "S&P 500", "QQQ": "Nasdaq 100", "IWM": "Small caps", "XHB": "Homebuilders"}),
               I("Rates", "Treasuries", {"TLT": "20y+ Treasuries", "IEF": "7-10y Treasuries", "SHY": "1-3y Treasuries"}),
               I("Forex", "USD", {"DX-Y.NYB": "Dollar index", "JPY=X": "USD/JPY"}),
               I("Commodities", "Gold", {"GC=F": "Gold"}),
               I("Crypto", "Bitcoin", {"BTC-USD": "Bitcoin"})]),
    T("nfp_strong_beat", "Strong US payrolls (beat)", "Economic", "United States",
      [r"\b(payrolls?|nonfarm|non-farm|jobs report|jobs data|jobs? growth|job gains|jobs market|hiring|employers|(add(s|ed|ing)?|creat(es|ed)|posts?) [\d,.]+ ?(k|,000|thousand)? ?(new )?(jobs|positions)|jobs blowout)\b",
       r"\b(beats?|beating|tops?|topping|surges?|stronger|blowout|above|jumps?|smash(es|ed)?|strong|robust|hot|huge|exceeds?|outstrips?|blow(s|ing)? past|trounc\w*|accelerat\w*|(1[5-9]\d|[2-9]\d\d)(,000| ?k| thousand))\b"],
      none=[r"\b(misses?|weaker|below|slows?|disappoint\w*|cuts?|layoffs?|lay off)\b", NON_US],
      commentary_ok=True,
      boost=[r"\b(expect|forecast|estimate|consensus)", r"\b\d{2,3},?000\b|\b\d{2,3}k\b"],
      impacts=[I("US Equities", "Banks, cyclicals & small caps", {"SPY": "S&P 500", "XLF": "Financials", "IWM": "Small caps", "XLY": "Cons. discretionary"}),
               I("Rates", "Treasuries", {"TLT": "20y+ Treasuries", "IEF": "7-10y Treasuries", "SHY": "1-3y Treasuries"}),
               I("Forex", "USD", {"DX-Y.NYB": "Dollar index", "JPY=X": "USD/JPY"}),
               I("Commodities", "Gold", {"GC=F": "Gold"})]),
    T("nfp_big_miss", "Weak US payrolls (miss)", "Economic", "United States",
      [r"\b(payrolls?|nonfarm|non-farm|jobs report|jobs data|hiring|job growth|job gains|employers|(add(s|ed|ing)?|creat(es|ed)|posts?) [\d,.]+ ?(k|,000|thousand)? ?(new )?(jobs|positions))\b",
       r"\b(misses?|missed|weaker|below|slows?|slowed|disappoint\w*|stalls?|falls? short|weak|shed|lost|negative)\b"],
      none=[r"\b(beats?|tops?|stronger|blowout)\b", NON_US],
      commentary_ok=True,
      boost=[r"\b(expect|forecast|estimate|consensus)", r"\b\d{2,3},?000\b|\b\d{2,3}k\b"],
      impacts=[I("US Equities", "Banks, cyclicals & small caps", {"SPY": "S&P 500", "XLF": "Financials", "IWM": "Small caps", "XLY": "Cons. discretionary"}),
               I("Rates", "Treasuries", {"TLT": "20y+ Treasuries", "IEF": "7-10y Treasuries", "SHY": "1-3y Treasuries"}),
               I("Forex", "USD", {"DX-Y.NYB": "Dollar index", "JPY=X": "USD/JPY"}),
               I("Commodities", "Gold", {"GC=F": "Gold"})]),
    T("megacap_earnings_beat", "Mega-cap earnings beat", "Economic", "United States",
      [r"\b(earnings|profit|revenue|results|quarter|eps|sales)\b", r"\b(beats?|tops?|surpass\w*|exceeds?|crush\w*|above (estimates|expectations)|record)\b"],
      none=[r"\b(misses?|disappoint\w*|falls? short|below|weak guidance|cuts? (guidance|forecast))\b"],
      commentary_ok=True, company_specific=True, benchmark="QQQ",
      default_tickers=["AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "NFLX"],
      impacts=[I("Single Stocks", "Reporting company (vs Nasdaq 100)", {})],
      note="Measured as abnormal return vs QQQ from the first session after the report."),
    T("megacap_earnings_miss", "Mega-cap earnings miss", "Economic", "United States",
      [r"\b(earnings|profit|revenue|results|quarter|eps|sales|guidance|outlook|forecast|subscribers?|subscriber growth)\b", r"\b(misses?|missed|disappoint\w*|falls? short|below (estimates|expectations)|weak|cuts? (guidance|forecast)|warns?)\b"],
      none=[r"\b(beats?|tops?|record)\b", r"\?", r"^(what|why|how)\b", r"\b(could|might|may|likely|ahead of|preview|expected to)\b"],
      spec_ok=True, company_specific=True, benchmark="QQQ",
      default_tickers=["AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA", "NFLX"],
      impacts=[I("Single Stocks", "Reporting company (vs Nasdaq 100)", {})],
      note="Measured as abnormal return vs QQQ from the first session after the report."),
    T("em_sovereign_downgrade", "EM sovereign credit downgrade", "Economic", "Emerging Markets",
      [r"\b(downgrades?|downgraded|cuts? .{0,20}rating|junk)\b", r"\b(s&p|moody'?s|fitch|sovereign|credit rating)\b"],
      none=[r"\bupgrade"], company_specific=True, benchmark="EEM",
      default_tickers=["EWZ", "TUR", "EZA", "EWW", "INDA"],
      impacts=[I("Single Stocks", "Country ETF (vs EM index)", {})],
      note="Country ETF abnormal return vs EEM."),

    # ── POLITICAL ────────────────────────────────────────────────────────────
    T("us_tariffs_on_china", "US tariffs imposed on China", "Political", "United States / China",
      [r"\b(tariffs?|duties|levies)\b", r"\b(china|chinese|beijing)\b", r"\b(impose[sd]?|announce[sd]?|raises?|raised|hikes?|hiked|slaps?|additional|increase[sd]?|levies|takes? effect|go(es)? into effect|sign(s|ed)? order)\b"],
      none=[r"\b(truce|deal|pause[sd]?|suspen\w*|lift\w*|remove[sd]?|exempt\w*|cuts?|lowers?|retaliat\w*|delay\w*|postpone\w*)\b",
            r"\bon (us|u\.s\.|american) (goods|products|imports|exports)\b"],
      boost=[r"\b(trump|biden|white house|ustr|commerce)\b", r"\b\d{2,3}%"],
      impacts=[I("US Equities", "China-exposed US names", {"AAPL": "Apple", "QCOM": "Qualcomm", "MU": "Micron", "WMT": "Walmart", "TGT": "Target", "GM": "GM", "DE": "Deere", "ADM": "ADM"}),
               I("Asia Equities", "China & Taiwan", {"FXI": "China large-cap", "BABA": "Alibaba", "JD": "JD.com", "TSM": "TSMC", "^HSI": "Hang Seng"}),
               I("Commodities", "Soybeans & copper", {"ZS=F": "Soybeans", "HG=F": "Copper"}),
               I("Forex", "USD/CNY", {"CNY=X": "USD/CNY"})]),
    T("us_china_trade_deal", "US–China trade deal / truce", "Political", "United States / China",
      [r"\b(trade|tariffs?)\b", r"\b(china|chinese|beijing|xi)\b", r"\b(deal|truce|agreement|accord|pause|ceasefire|phase one|framework)\b",
       r"\b(sign(s|ed)?|reach(es|ed)?|agree(s|d)?|strike(s)?|struck|clinch(es|ed)?|announce(s|d)?|seal(s|ed)?|unveil(s|ed)?|extend(s|ed)?|pause(s|d))\b"],
      none=[r"\b(collapse[sd]?|stall\w*|break ?down|no deal|fails?|honou?r|terms of|commitments?|meet|falls? short|warns?)\b"],
      impacts=[I("US Equities", "China-exposed US names", {"AAPL": "Apple", "QCOM": "Qualcomm", "MU": "Micron", "NKE": "Nike", "SBUX": "Starbucks", "DE": "Deere", "ADM": "ADM"}),
               I("Asia Equities", "China & Taiwan", {"FXI": "China large-cap", "BABA": "Alibaba", "JD": "JD.com", "TSM": "TSMC", "^HSI": "Hang Seng"}),
               I("Commodities", "Soybeans & copper", {"ZS=F": "Soybeans", "HG=F": "Copper"}),
               I("Forex", "USD/CNY", {"CNY=X": "USD/CNY"})]),
    T("us_gov_shutdown", "US government shutdown", "Political", "United States",
      [r"\b(government|federal)\b", r"\bshut ?downs?\b"],
      none=[r"\b(ends?|ended|averts?|averted|avoid\w*|reopens?|deal to)\b"],
      impacts=[I("US Equities", "Federal contractors & travel", {"LMT": "Lockheed", "NOC": "Northrop", "BAH": "Booz Allen", "SAIC": "SAIC", "LDOS": "Leidos", "UAL": "United Airlines", "DAL": "Delta"}),
               I("US Equities", "Broad market", {"SPY": "S&P 500", "IWM": "Small caps"}),
               I("Forex", "Dollar index", {"DX-Y.NYB": "Dollar index"}),
               I("Commodities", "Gold", {"GC=F": "Gold"})]),
    T("uk_snap_election", "UK snap election called", "Political", "United Kingdom",
      [r"\b(uk|britain|british|sunak|starmer|johnson|may)\b", r"\b(snap|early|general) election\b", r"\b(call\w*|announce\w*|trigger\w*)\b"],
      impacts=[I("EU Equities", "UK equities", {"^FTSE": "FTSE 100", "^FTMC": "FTSE 250", "LLOY.L": "Lloyds", "BARC.L": "Barclays", "NG.L": "National Grid", "BA.L": "BAE Systems"}),
               I("Forex", "GBP/USD", {"GBPUSD=X": "GBP/USD"})]),

    # ── GEOPOLITICAL ─────────────────────────────────────────────────────────
    T("middle_east_conflict", "Middle East military escalation", "Geopolitical", "Middle East / Global",
      [r"\b(israel|iran|gaza|hamas|hezbollah|saudi|iraq|syria|yemen|houthis?|lebanon|tehran|middle east|gulf)\b",
       r"\b(strikes|struck|attacks|attacked|target(s|ed)|launch(es|ed)|fires|fired|bombs|bombed|bombing|invades?|invaded|airstrikes?|retaliat(es|ed)|shoots? down|shot down|downed|declares? war|hits|enters?)\b"],
      none=[r"\b(ceasefire|truce|peace|talks|hostage deal|de-?escalat\w*|sanction\w*|satellite|election|diplomacy)\b", r"\b(red sea|shipping|tankers?|vessels?|suez)\b",
            r"\b(central bank|bank of england|fed|ecb|interest rates?|oil prices?)\b"],
      boost=[r"\boil\b", r"\b(launch\w*|killed|declares?)\b"],
      impacts=[I("Commodities", "Oil & gold", {"CL=F": "WTI crude", "BZ=F": "Brent crude", "GC=F": "Gold"}),
               I("US Equities", "Energy & defence", {"XLE": "Energy", "LMT": "Lockheed", "NOC": "Northrop", "RTX": "RTX"}),
               I("US Equities", "Airlines & broad market", {"UAL": "United Airlines", "DAL": "Delta", "SPY": "S&P 500"})]),
    T("russia_ukraine_escalation", "Russia–Ukraine escalation", "Geopolitical", "Europe / Russia",
      [r"\b(russia\w*|ukrain\w*|kremlin|putin|moscow|kyiv)\b",
       r"\b(invades?|invaded|launches (full-scale|an? invasion)|mobili[sz](es|ed|ation)|annex(es|ed|ation)|nuclear (strike|weapons? use|alert|drills?)|declares? (war|martial law)|(largest|biggest|massive|unprecedented) (air ?strike|attack|missile|drone)\w*|nato (territory|member)|nord stream)\b"],
      none=[r"\b(ceasefire|peace|talks|truce|negotiat\w*|weekly|days? \d|root cause|casualt\w*|lost [\d,]+ troops)\b"],
      impacts=[I("EU Equities", "European equities", {"^STOXX50E": "Euro Stoxx 50", "^GDAXI": "DAX", "RHM.DE": "Rheinmetall", "BA.L": "BAE Systems"}),
               I("Commodities", "Energy, grains & gold", {"TTF=F": "EU gas (TTF)", "ZW=F": "Wheat", "BZ=F": "Brent crude", "GC=F": "Gold"}),
               I("Forex", "EUR/USD", {"EURUSD=X": "EUR/USD"})]),
    T("shipping_lane_disruption", "Shipping lane disruption", "Geopolitical", "Global",
      [r"\b(red sea|suez|hormuz|bab el-mandeb|panama canal|shipping lanes?|strait)\b",
       r"\b(attacks?|block\w*|seiz\w*|disrupt\w*|reroute\w*|divert\w*|halt\w*|closed|closure|strikes?|stuck|restrict\w*)\b"],
      boost=[r"\b(houthis?|tankers?|maersk|container)\b"],
      impacts=[I("Commodities", "Crude oil", {"BZ=F": "Brent crude", "CL=F": "WTI crude"}),
               I("EU Equities", "Container shipping", {"MAERSK-B.CO": "Maersk", "HLAG.DE": "Hapag-Lloyd"}),
               I("US Equities", "Energy & shippers", {"XLE": "Energy", "MATX": "Matson"})]),
    T("north_korea_missile", "North Korea ICBM / nuclear test", "Geopolitical", "East Asia",
      [r"\b(north korea\w*|pyongyang|dprk|kim jong)\b", r"\b(launch(es|ed)|fires|fired|test(s|ed)|conducts?|conducted)\b", r"\b(missiles?|icbm|nuclear|ballistic|rocket|satellite)\b"],
      impacts=[I("Asia Equities", "Korea & Japan", {"^KS11": "KOSPI", "^N225": "Nikkei 225", "EWY": "Korea ETF"}),
               I("Forex", "Won & yen vs USD", {"KRW=X": "USD/KRW", "JPY=X": "USD/JPY"}),
               I("Commodities", "Gold", {"GC=F": "Gold"})]),

    # ── INDUSTRIAL ───────────────────────────────────────────────────────────
    T("opec_cut", "OPEC+ production cut", "Industrial", "Global",
      [r"\bopec\+?", r"\b(cuts?|reduc\w*|curb\w*|lowers?|slash\w*|extends? (cuts?|curbs?))\b"],
      none=[r"\b(boost|increas\w*|raises?|hikes? output|unwind\w*|add\w* (barrels|supply))\b"],
      boost=[r"\b(output|production|supply|barrels|bpd)\b", r"\b(surprise|voluntary|agree\w*)\b"],
      impacts=[I("Commodities", "Crude oil", {"CL=F": "WTI crude", "BZ=F": "Brent crude"}),
               I("US Equities", "Oil producers & services", {"XLE": "Energy", "XOM": "Exxon", "CVX": "Chevron", "OXY": "Occidental", "COP": "ConocoPhillips", "SLB": "SLB", "HAL": "Halliburton"}),
               I("US Equities", "Airlines (fuel cost)", {"UAL": "United Airlines", "DAL": "Delta", "AAL": "American Airlines"})]),
    T("opec_increase", "OPEC+ production increase", "Industrial", "Global",
      [r"\bopec\+?", r"\b(boost\w*|increas\w*|raises?|hikes? output|unwind\w*|add\w*|ramps? up|pump more)\b"],
      none=[r"\b(cuts?|curb\w*|reduc\w*)\b"],
      boost=[r"\b(output|production|supply|barrels|bpd)\b"],
      impacts=[I("Commodities", "Crude oil", {"CL=F": "WTI crude", "BZ=F": "Brent crude"}),
               I("US Equities", "Oil producers & services", {"XLE": "Energy", "XOM": "Exxon", "CVX": "Chevron", "OXY": "Occidental", "COP": "ConocoPhillips", "SLB": "SLB", "HAL": "Halliburton"}),
               I("US Equities", "Airlines (fuel cost)", {"UAL": "United Airlines", "DAL": "Delta", "AAL": "American Airlines"})]),
    T("port_strike_disruption", "Major port strike / closure", "Industrial", "Global",
      [r"\b(ports?|dock\w*|longshore\w*|terminals?|harbou?r)\b", r"\b(strikes?|walkouts?|clos\w*|shut\w*|halt\w*|collaps\w*|lockdown|blocked)\b"],
      boost=[r"\b(ila|ilwu|union|west coast|east coast|shanghai|baltimore|rotterdam)\b"],
      impacts=[I("US Equities", "Import-heavy retailers", {"WMT": "Walmart", "TGT": "Target", "HD": "Home Depot", "NKE": "Nike", "BBY": "Best Buy"}),
               I("US Equities", "Shippers & logistics", {"MATX": "Matson", "ZIM": "ZIM", "FDX": "FedEx", "UNP": "Union Pacific"})]),
    T("us_drought_crop", "US drought / crop shortfall", "Industrial", "United States / Global",
      [r"\b(drought|crop|harvest|yield|usda|wasde|corn|soybeans?|wheat)\b",
       r"\b(drought|shortfall|cuts?|slash\w*|lowers?|fail\w*|withers?|damage\w*|heat ?wave|worst)\b"],
      none=[r"\b(record (crop|harvest)|bumper|raises? (yield|forecast))\b"],
      impacts=[I("Commodities", "Grains", {"ZC=F": "Corn", "ZS=F": "Soybeans", "ZW=F": "Wheat"}),
               I("US Equities", "Agribusiness & food", {"ADM": "ADM", "BG": "Bunge", "DE": "Deere", "TSN": "Tyson"})]),
    T("eu_gas_squeeze", "European gas supply squeeze", "Industrial", "European Union",
      [r"\b(gas|lng|nord stream|gazprom|ttf|pipeline)\b",
       r"\b(cut\w*|halt\w*|shut\w*|stops?|outages?|squeeze|shortage|disrupt\w*|sabotage|leaks?|suspend\w*|reduc\w*)\b"],
      boost=[r"\b(europe\w*|germany|eu|russia\w*|norway|ukraine)\b"],
      impacts=[I("Commodities", "Natural gas", {"TTF=F": "EU gas (TTF)", "NG=F": "US natural gas"}),
               I("EU Equities", "Utilities & chemicals", {"RWE.DE": "RWE", "EOAN.DE": "E.ON", "BAS.DE": "BASF", "^GDAXI": "DAX"}),
               I("Forex", "EUR/USD", {"EURUSD=X": "EUR/USD"})]),

    # ── TECHNOLOGY ───────────────────────────────────────────────────────────
    T("big_tech_antitrust_ruling", "Antitrust ruling against big tech", "Technology", "United States / EU",
      [r"\b(antitrust|monopol\w*|competition|doj|ftc|european commission|dma)\b",
       r"\b(rul\w*|fine[sd]?|verdict|judge|court|orders?|guilty|violat\w*|breakup|break up|remed\w*|decision)\b"],
      boost=[r"\b(google|alphabet|apple|meta|facebook|amazon|microsoft)\b"],
      company_specific=True, benchmark="QQQ",
      default_tickers=["GOOGL", "AAPL", "META", "AMZN", "MSFT"],
      impacts=[I("Single Stocks", "Targeted company (vs Nasdaq 100)", {})]),
    T("ai_frontier_launch", "Frontier AI model / product launch", "Technology", "United States / Global",
      [r"\b(ai|artificial intelligence|model|chatbot|llm|gpt|gemini|claude|deepseek|llama|grok)\b",
       r"\b(launch\w*|release[sd]?|unveil\w*|debut\w*|introduc\w*|rolls? out|announce[sd]?)\b"],
      boost=[r"\b(openai|anthropic|google|deepmind|meta|xai|deepseek|frontier|reasoning)\b"],
      company_specific=True, benchmark="QQQ",
      default_tickers=["NVDA", "MSFT", "GOOGL", "META"],
      impacts=[I("Single Stocks", "AI-exposed companies (vs Nasdaq 100)", {})]),
    T("chip_export_restriction", "Semiconductor export restriction", "Technology", "United States / China",
      [r"\b(chips?|semiconductors?|nvidia|gpu|asml|lithography|ai accelerators?)\b",
       r"\b(export (controls?|curbs?|ban|restrictions?|rules?)|entity list|blacklist\w*|bans? .{0,20}(sales?|exports?) to china|restrict\w* .{0,25}china)\b"],
      none=[r"\b(eases?|lifts?|lifted|relax\w*|resume\w*|approv\w*)\b"],
      company_specific=True, benchmark="QQQ",
      default_tickers=["NVDA", "AMD", "AMAT", "LRCX", "KLAC", "ASML"],
      impacts=[I("Single Stocks", "Chipmakers & equipment (vs Nasdaq 100)", {})]),

    # ── SOCIAL / REGULATORY ──────────────────────────────────────────────────
    T("fda_approval", "FDA drug approval", "Social", "United States",
      [r"\b(fda|food and drug administration)\b", r"\b(approv\w*|clear\w*|green ?light\w*|authoriz\w*|grants?)\b"],
      none=[r"\b(rejects?|declin\w*|complete response|crl|fail\w*|delay\w*|recall\w*|warns?|warning)\b",
            r"\b(toward|towards|advances|seeks|submits?|files? for|accepts? (filing|application)|priority review|breakthrough (therapy )?designation|path to)\b"],
      commentary_ok=True, company_specific=True, benchmark="XBI",
      impacts=[I("Single Stocks", "Sponsor company (vs biotech index)", {})]),
    T("fda_trial_failure", "FDA rejection / Phase 3 failure", "Social", "United States",
      [r"\b(fda|phase 3|phase iii|trial|study|complete response|crl)\b",
       r"\b(fail\w*|rejects?|rejected|declin\w*|halt\w*|misses? .{0,15}(endpoint|goal)|did not meet|futility|complete response letter|crl|discontinu\w*)\b"],
      commentary_ok=True, company_specific=True, benchmark="XBI",
      impacts=[I("Single Stocks", "Sponsor company (vs biotech index)", {})]),
    T("major_recall", "Major product recall / grounding", "Social", "United States / Global",
      [r"\b(recall\w*|grounds?|grounded|grounding)\b"],
      boost=[r"\b(million|nhtsa|fda|faa|safety|vehicles|aircraft)\b"],
      none=[r"\b(recalls? (that|how|when)|i recall)\b"],
      company_specific=True, benchmark="SPY",
      impacts=[I("Single Stocks", "Recalling company (vs S&P 500)", {})]),
    T("eu_climate_regulation", "EU carbon / climate regulation", "Social", "European Union",
      [r"\b(eu|european|brussels|commission|parliament)\b",
       r"\b(carbon|climate|emissions?|ets|cbam|fit for 55|green deal|combustion engine|net zero)\b",
       r"\b(rules?|law|regulation|tax|ban|levy|target|proposal|propos\w*|adopt\w*|approv\w*|vote[sd]?|unveil\w*)\b"],
      impacts=[I("EU Equities", "Utilities & renewables", {"RWE.DE": "RWE", "ENEL.MI": "Enel", "IBE.MC": "Iberdrola", "ORSTED.CO": "Ørsted"}),
               I("EU Equities", "Heavy industry & autos", {"MT.AS": "ArcelorMittal", "HEI.DE": "Heidelberg Materials", "VOW3.DE": "Volkswagen", "MBG.DE": "Mercedes-Benz"})]),
    T("currency_devaluation", "EM currency devaluation / peg break", "Social", "Emerging Markets",
      [r"\b(devalu\w*|depegs?|peg|float\w*|currency crisis|plunges?|collaps\w*|record low)\b",
       r"\b(currency|yuan|renminbi|peso|lira|naira|pound|rupee|rupiah|real|rand|tenge|franc)\b"],
      company_specific=True,
      impacts=[I("Forex", "Affected currency (USD/xxx)", {})],
      note="Positive move = USD/xxx rising = local currency weakening."),
]

TYPES_BY_ID = {t["id"]: t for t in EVENT_TYPES}

# Company/issuer name -> Yahoo ticker, for company-specific signals.
COMPANY_ALIASES = {
    "apple": "AAPL", "microsoft": "MSFT", "alphabet": "GOOGL", "google": "GOOGL", "amazon": "AMZN",
    "meta": "META", "facebook": "META", "nvidia": "NVDA", "tesla": "TSLA", "netflix": "NFLX",
    "amd": "AMD", "advanced micro devices": "AMD", "intel": "INTC", "qualcomm": "QCOM", "broadcom": "AVGO",
    "micron": "MU", "applied materials": "AMAT", "lam research": "LRCX", "kla": "KLAC", "asml": "ASML",
    "tsmc": "TSM", "taiwan semiconductor": "TSM", "oracle": "ORCL", "salesforce": "CRM", "ibm": "IBM",
    "eli lilly": "LLY", "lilly": "LLY", "pfizer": "PFE", "merck": "MRK", "moderna": "MRNA",
    "biogen": "BIIB", "eisai": "4523.T", "novo nordisk": "NVO", "amgen": "AMGN", "gilead": "GILD",
    "vertex": "VRTX", "regeneron": "REGN", "abbvie": "ABBV", "bristol myers": "BMY", "bristol-myers": "BMY",
    "johnson & johnson": "JNJ", "j&j": "JNJ", "astrazeneca": "AZN", "novartis": "NVS", "roche": "RHHBY",
    "sanofi": "SNY", "gsk": "GSK", "biontech": "BNTX", "sarepta": "SRPT", "alnylam": "ALNY",
    "vertex pharmaceuticals": "VRTX", "madrigal": "MDGL",
    "boeing": "BA", "general motors": "GM", "gm": "GM", "ford": "F", "toyota": "TM", "stellantis": "STLA",
    "volkswagen": "VWAGY", "honda": "HMC", "abbott": "ABT", "philips": "PHG", "samsung": "005930.KS",
    "walmart": "WMT", "target": "TGT", "nike": "NKE", "starbucks": "SBUX", "mcdonald's": "MCD",
    "jpmorgan": "JPM", "goldman": "GS", "bank of america": "BAC", "openai": "MSFT", "anthropic": "AMZN",
    "deepseek": "NVDA", "xai": "TSLA",
}

# Currency word -> Yahoo USD/xxx symbol for devaluation signals.
CURRENCY_ALIASES = {
    "egypt": "EGP=X", "turkey": "TRY=X", "turkish": "TRY=X", "nigeria": "NGN=X", "argentina": "ARS=X",
    "china": "CNY=X", "chinese": "CNY=X", "kazakhstan": "KZT=X", "swiss": "CHF=X", "snb": "CHF=X",
    "india": "INR=X", "indonesia": "IDR=X", "south africa": "ZAR=X", "brazil": "BRL=X", "russia": "RUB=X", "ruble": "RUB=X", "rouble": "RUB=X",
    "yuan": "CNY=X", "renminbi": "CNY=X", "lira": "TRY=X", "naira": "NGN=X", "egyptian pound": "EGP=X",
    "argentine peso": "ARS=X", "peso": "ARS=X", "rupee": "INR=X", "rupiah": "IDR=X", "rand": "ZAR=X",
    "tenge": "KZT=X", "real": "BRL=X", "swiss franc": "CHF=X", "franc": "CHF=X", "yen": "JPY=X", "won": "KRW=X",
}

# Publisher credibility (0-100) for the source_credibility score component.
SOURCE_CREDIBILITY = {
    "reuters": 95, "bloomberg": 95, "associated press": 92, "ap news": 92, "federal reserve": 99, "fda": 99,
    "sec": 98, "ecb": 99, "financial times": 92, "wall street journal": 92, "wsj": 92, "the economist": 88,
    "cnbc": 85, "marketwatch": 82, "barron's": 85, "bbc": 88, "new york times": 88, "washington post": 86,
    "the guardian": 82, "axios": 82, "politico": 82, "nikkei": 88, "yahoo finance": 75, "fortune": 78,
    "forbes": 70, "business insider": 68, "investopedia": 60, "seeking alpha": 55, "motley fool": 45,
    "benzinga": 60, "zacks": 50, "fxstreet": 60, "oilprice.com": 62, "investing.com": 65, "al jazeera": 80,
    "fox business": 72, "the hill": 72, "nasdaq": 70, "s&p global": 90, "spglobal": 90, "fiercebiotech": 78,
    "fierce pharma": 78, "stat": 85, "endpoints": 80, "biopharma dive": 78,
}

# Economic scale of the geography (0-100) for the geo_scope score component.
GEO_SCOPE = {
    "Global": 100, "United States": 95, "United States / China": 100, "United States / Global": 98,
    "United States / EU": 95, "European Union": 85, "Europe / Russia": 85, "Middle East / Global": 92,
    "United Kingdom": 65, "East Asia": 70, "Emerging Markets": 55,
}

SCORE_WEIGHTS = [
    {"name": "event_match", "pct": 30, "desc": "Classifier confidence that the headline maps to a known event type (rule hits + boosts; Claude-confirmed when enabled)."},
    {"name": "historical_edge", "pct": 25, "desc": "Strength of the backtested reaction for this event type: direction consistency and t-statistic at the optimal window, shrunk by sample size."},
    {"name": "source_credibility", "pct": 20, "desc": "Static weight per publisher. Reuters/Bloomberg/official releases 95–99, major outlets 80–92, aggregators/blogs 45–65, unknown 50."},
    {"name": "novelty", "pct": 15, "desc": "Articles about the same event (same type, and same company where relevant, within 24h) are clustered into one signal. A type that already fired in the previous 7 days scores −25 per prior cluster (floor 20), so ongoing stories rank below fresh shocks."},
    {"name": "geo_scope", "pct": 10, "desc": "Economic scale of the affected geography: US/China/global 95–100, EU 85, UK 65, single EM 55."},
]
