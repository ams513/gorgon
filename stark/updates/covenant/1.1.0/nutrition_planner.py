"""Stark Covenant - nutrition_planner: evidence-based GENERAL nutrition support (not medical advice).

Standard library only, Python 3.10+.

What it does
  * Meal plans for 1-14 days that aim at daily targets for energy, protein, fibre, sodium, potassium and
    secondary nutrients (calcium, iron, magnesium, vitamin C, B12, folate, saturated fat, macronutrient ranges,
    fruit + vegetables in grams), built from a compact offline subset of USDA FoodData Central SR Legacy
    (data/fdc_subset.json; public domain, see data/LICENSES.md).
  * The optimiser is a deterministic constrained local search over half-servings: it starts from an empty day
    and keeps making the single best move (add half a serving, remove half a serving, or swap half a serving of
    one food for another) that lowers a penalty made of squared relative shortfalls/excesses against each
    target, an optional budget limit and small variety / preference terms. Hard constraints (allergens,
    intolerances, diet rules, foods to avoid, clinical exclusions, per-food serving caps) remove foods from the
    candidate pool BEFORE optimising, so an excluded food can never appear; the result is checked again after.
  * Preferences: vegetarian, vegan, pescatarian, halal, kosher (no meat with dairy in the same meal), no pork,
    no beef, budget per day, liked / disliked foods, local foods, rough cuisine starting points.
  * Shopping list for the whole plan, plain-language explanations, sources for every target.

Safety rules (never relaxed)
  * NOTICE is in every result: general information, not medical advice.
  * Clinical flags - CKD, diabetes, pregnancy / lactation, eating disorders, warfarin (vitamin K), MAOIs
    (tyramine), grapefruit-interacting medicines, enteral (tube) nutrition, and anyone under 19 - are never
    auto-planned. They add REVIEW_NOTE ("review with your clinician/dietitian"). Eating disorders and enteral
    nutrition WITHHOLD the plan entirely (no numbers, no foods). Every other flag returns a DRAFT with
    status "draft_requires_clinician_signoff" that only sign_off() (a clinician, verified by the caller, e.g.
    clinic_app's PIN check) can turn into "clinician_approved".
  * Personalisation (using age, sex, body size, activity, chart allergies/medicines/problems, remembering
    likes/dislikes) happens only under consent. With consent_center mounted on the clinic, its live
    is_active(patient, "nutrition") answer is final, remembered likes/dislikes are stored in consent_center's
    own data-minimised "nutrition" fields (food_preferences / food_dislikes, deleted on withdrawal) and its
    dietary_pattern / cultural_or_religious_practices fields pre-fill the diet. consent_center installed but
    not attached = no consent can be confirmed = OFF. Only when consent_center is not installed at all is a
    local consent ledger used, and it is OFF until a staff member records the person's yes.
    Without consent the plan uses general adult reference targets and nothing about the person is stored.
    Allergies, intolerances and medicines typed into the request are still honoured as one-off safety
    exclusions (never stored) - safety does not wait for consent.

Sources (targets cite these; see SOURCES)
  NASEM / IOM Dietary Reference Intakes (DRI summary tables): Macronutrients 2002/2005 (doi:10.17226/10490),
  Sodium and Potassium 2019 (doi:10.17226/25353), Calcium and Vitamin D 2011 (doi:10.17226/13050),
  Vitamin A, K, Iron ... 2001 (doi:10.17226/10026), Ca, P, Mg ... 1997 (doi:10.17226/5776), B vitamins and
  folate 1998 (doi:10.17226/6015), Vitamin C ... 2000 (doi:10.17226/9810), Energy 2023 (doi:10.17226/26818).
  WHO: Sodium intake guideline 2012; Potassium intake guideline 2012; Sugars intake guideline 2015; Saturated
  and trans fatty acid guideline 2023; Healthy diet fact sheet (>= 400 g fruit and vegetables a day).
  FDA: Daily Values and the 2,000 kcal reference (21 CFR 101.9(c)); Grapefruit juice and medicines consumer
  update. Energy (personalised) uses the IOM 2005 EER equations for adults 19+ (the 2023 NASEM equations
  replace them; the 2005 ones are kept because they need only age, sex, height, weight and activity level).

Integration with clinic_app (clinic_app.py is not edited by this module)
  mount(app) registers JSON routes in the ROUTES table of the module that defines `app` (works when
  clinic_app runs as __main__ too), so clinic_app's session, CSRF, Host/Origin and LAN checks all apply:
      GET  /api/nutrition/info        notice, sources, food list (any signed-in user)
      POST /api/nutrition/plan        {request: {...}, patient?: id}  (chart use needs clin.read + consent)
      POST /api/nutrition/signoff     {plan: {...}, pin}               (note.sign + clinician PIN)
      POST /api/nutrition/consent     {subject, grant: bool, statement} (local ledger; demo.write; 409 when
                                      consent_center is installed - use its /api/consent routes)
      POST /api/nutrition/feedback    {subject, food, liked: bool}      (demo.write; stored only under consent)
  With consent_center mounted (mount it BEFORE this module), mount() also registers an on_withdraw callback
  and a data holder ("nutrition_planner") so withdrawal, subject-access export and erasure reach this module.
  The clinic_app owner enables it with two lines in CovenantApp.__init__ (after make_comms):
      for _n in ("nutrition_planner", "coverage_navigator"):
          _m = optional(_n); setattr(self, _n, _m.mount(self) if _m and hasattr(_m, "mount") else None)
  (wrap in try/except + self.clinic.health(...) like make_comms, and add both names to OPTIONAL_MODULES).
  Stored data (consent ledger, feedback) lives in <data_root>/nutrition (vault-encrypted when the clinic has a
  vault) or <data_root>/nutrition_no_vault, mirroring emergency_comms.

Honest limits
  * 106 common foods, not a full food database; nutrient values are SR Legacy (2018) per 100 g and some are
    not reported (the plan says which). Prices are illustrative placeholders - enter local prices.
  * Total sugars are reported; WHO's limit is on FREE sugars, which SR Legacy does not separate, so no sugar
    limit is enforced (the food list has no added-sugar foods).
  * The local search finds a good plan, not a proven optimum. Targets are population reference values for
    healthy people; they are not individual prescriptions.
  * Allergen tags describe the food, not factory cross-contact: always read the label.
"""
from __future__ import annotations

import copy
import datetime as _dt
import hashlib
import importlib
import json
import math
import os
import re
import sys
import threading

APP_NAME = "Stark Covenant"
MODULE_VERSION = "1.0.0"
HERE = os.path.dirname(os.path.abspath(__file__))
FOOD_FILE = os.path.join(HERE, "data", "fdc_subset.json")

NOTICE = ("General nutrition information only, based on public reference data (USDA FoodData Central, NASEM "
          "Dietary Reference Intakes, WHO guidance). It is not medical advice, diagnosis or treatment and does "
          "not replace a registered dietitian or clinician. Always read food labels for allergens.")
REVIEW_NOTE = ("Review with your clinician/dietitian: a health condition or medicine was noted, so this is not "
               "an automatic plan. Any draft below needs clinician sign-off before it is used.")
WITHHELD_NOTE = ("No meal plan is generated for this situation. Nutrition here needs to be planned with your "
                 "treating clinician or dietitian.")
CONSENT_PURPOSE = "nutrition"   # the consent_center purpose key

SOURCES = {
    "dri_macro": "NASEM (IOM). Dietary Reference Intakes for Energy, Carbohydrate, Fiber, Fat, Fatty Acids, "
                 "Cholesterol, Protein, and Amino Acids. National Academies Press, 2002/2005. doi:10.17226/10490",
    "dri_energy_2023": "NASEM. Dietary Reference Intakes for Energy. National Academies Press, 2023. "
                       "doi:10.17226/26818",
    "dri_na_k": "NASEM. Dietary Reference Intakes for Sodium and Potassium. National Academies Press, 2019. "
                "doi:10.17226/25353",
    "dri_ca_d": "IOM. Dietary Reference Intakes for Calcium and Vitamin D. National Academies Press, 2011. "
                "doi:10.17226/13050",
    "dri_micro": "IOM. Dietary Reference Intakes for Vitamin A, Vitamin K, Arsenic, Boron, Chromium, Copper, "
                 "Iodine, Iron, Manganese, Molybdenum, Nickel, Silicon, Vanadium, and Zinc. 2001. "
                 "doi:10.17226/10026",
    "dri_ca_p_mg": "IOM. Dietary Reference Intakes for Calcium, Phosphorus, Magnesium, Vitamin D, and Fluoride. "
                   "1997. doi:10.17226/5776",
    "dri_b": "IOM. Dietary Reference Intakes for Thiamin, Riboflavin, Niacin, Vitamin B6, Folate, Vitamin B12, "
             "Pantothenic Acid, Biotin, and Choline. 1998. doi:10.17226/6015",
    "dri_c": "IOM. Dietary Reference Intakes for Vitamin C, Vitamin E, Selenium, and Carotenoids. 2000. "
             "doi:10.17226/9810",
    "who_sodium": "WHO. Guideline: Sodium intake for adults and children. Geneva, 2012 (< 2 g sodium / day).",
    "who_potassium": "WHO. Guideline: Potassium intake for adults and children. Geneva, 2012 (>= 90 mmol/day).",
    "who_sugars": "WHO. Guideline: Sugars intake for adults and children. Geneva, 2015 (free sugars < 10% "
                  "energy).",
    "who_fat": "WHO. Saturated fatty acid and trans-fatty acid intake for adults and children: WHO guideline. "
               "Geneva, 2023 (saturated fat <= 10% energy).",
    "who_healthy_diet": "WHO. Healthy diet, fact sheet (>= 400 g fruit and vegetables a day, excluding starchy "
                        "roots).",
    "fda_dv": "US FDA. Food labeling: Daily Values and the 2,000 calorie reference, 21 CFR 101.9(c).",
    "fda_grapefruit": "US FDA. Grapefruit Juice and Some Drugs Don't Mix (consumer update).",
    "usda_fdc": "USDA ARS. FoodData Central: SR Legacy, April 2018. fdc.nal.usda.gov (public domain, CC0).",
}

NUTRIENT_INFO = {   # key: (label, unit, why it matters - plain language, general)
    "energy_kcal": ("Energy", "kcal", "Energy keeps your body running; too little or too much over time "
                                      "changes body weight."),
    "protein_g": ("Protein", "g", "Protein builds and repairs muscle, skin and other tissues."),
    "fibre_g": ("Fibre", "g", "Fibre supports regular digestion and is linked with lower risk of heart "
                              "disease and type 2 diabetes."),
    "sodium_mg": ("Sodium", "mg", "Eating less sodium (salt) helps keep blood pressure down."),
    "potassium_mg": ("Potassium", "mg", "Potassium from foods helps balance the effect of sodium on blood "
                                        "pressure."),
    "calcium_mg": ("Calcium", "mg", "Calcium is needed for bones and teeth."),
    "iron_mg": ("Iron", "mg", "Iron carries oxygen in the blood."),
    "magnesium_mg": ("Magnesium", "mg", "Magnesium helps muscles, nerves and energy use."),
    "vitc_mg": ("Vitamin C", "mg", "Vitamin C supports the immune system and helps the body absorb iron "
                                   "from plants."),
    "b12_ug": ("Vitamin B12", "ug", "B12 keeps nerves and blood cells healthy; plant-only diets need a "
                                    "fortified food or supplement."),
    "folate_dfe_ug": ("Folate", "ug DFE", "Folate is needed to make new cells, especially before and during "
                                          "pregnancy."),
    "vitk_ug": ("Vitamin K", "ug", "Vitamin K helps blood clot; people on warfarin need a STEADY amount, "
                                   "agreed with their clinician."),
    "satfat_g": ("Saturated fat", "g", "Keeping saturated fat low helps keep LDL cholesterol down."),
    "fat_g": ("Total fat", "g", "Fats give energy and help absorb some vitamins."),
    "carb_g": ("Carbohydrate", "g", "Carbohydrates are the body's main fuel."),
    "sugars_g": ("Total sugars", "g", "Includes natural sugars in fruit and milk."),
    "phosphorus_mg": ("Phosphorus", "mg", "Phosphorus works with calcium in bones."),
    "fruit_veg_g": ("Fruit and vegetables", "g", "Eating at least 400 g of fruit and vegetables a day is "
                                                 "linked with lower risk of many diseases."),
    "cost_usd": ("Estimated cost", "USD", "Based on illustrative prices unless you enter your own."),
}

# --- clinical flags ---------------------------------------------------------------------------------------
CLINICAL_FLAGS = {
    "ckd": {"label": "Chronic kidney disease", "withhold": False,
            "terms": ("ckd", "chronic kidney", "kidney disease", "renal failure", "renal insufficiency",
                      "dialysis", "nephropathy", "kidney transplant"),
            "why": "Protein, potassium, phosphorus, sodium and fluid needs in kidney disease are set "
                   "individually; general targets can be harmful.",
            "draft_rule": "Potassium, phosphorus and protein minimums are removed from this draft; the "
                          "clinician/dietitian sets them."},
    "diabetes": {"label": "Diabetes", "withhold": False,
                 "terms": ("diabetes", "diabetic", "t1dm", "t2dm", "dm1", "dm2", "insulin", "metformin",
                           "glipizide", "glyburide", "glibenclamide", "glimepiride", "gliclazide", "sitagliptin",
                           "empagliflozin", "dapagliflozin", "canagliflozin", "semaglutide", "liraglutide",
                           "dulaglutide", "tirzepatide", "pioglitazone"),
                 "why": "Carbohydrate amount and timing interact with diabetes medicines (risk of low or high "
                        "blood glucose).",
                 "draft_rule": "Carbohydrate per meal is shown for the clinician to adjust."},
    "pregnancy": {"label": "Pregnancy or breastfeeding", "withhold": False,
                  "terms": ("pregnan", "lactating", "lactation", "breastfeeding", "breast-feeding",
                            "postpartum"),
                  "why": "Needs for energy, protein, folate, iron and others change, and some foods carry "
                         "listeria or mercury risks.",
                  "draft_rule": "Pregnancy/lactation DRIs are used; soft cheeses and dry-cured meats are "
                                "excluded."},
    "eating_disorder": {"label": "Eating disorder (current or past)", "withhold": True,
                        "terms": ("anorexia", "bulimia", "binge eating", "binge-eating", "arfid",
                                  "eating disorder", "orthorexia", "osfed", "ednos"),
                        "why": "Calorie targets and food rules can harm recovery; plans belong to the "
                               "treatment team.",
                        "draft_rule": "No plan, no numbers."},
    "warfarin_vitk": {"label": "Warfarin / vitamin K antagonist", "withhold": False,
                      "terms": ("warfarin", "coumadin", "jantoven", "marevan", "acenocoumarol", "sintrom",
                                "phenprocoumon", "vitamin k antagonist"),
                      "why": "Changes in vitamin K intake change how warfarin works; intake should stay steady.",
                      "draft_rule": "Vitamin K is shown for every day and kept close to day 1 (+/- 20%)."},
    "maoi_tyramine": {"label": "MAOI medicine (tyramine)", "withhold": False,
                      "terms": ("maoi", "phenelzine", "nardil", "tranylcypromine", "parnate", "isocarboxazid",
                                "marplan", "selegiline", "emsam", "rasagiline", "safinamide", "linezolid",
                                "moclobemide", "methylene blue", "procarbazine"),
                      "why": "Tyramine-rich foods (aged cheese, cured meats, fermented soy, sauerkraut) can cause "
                             "a dangerous rise in blood pressure with MAOIs.",
                      "draft_rule": "High-tyramine foods are excluded from this draft."},
    "grapefruit": {"label": "Medicine that interacts with grapefruit", "withhold": False,
                   "terms": ("simvastatin", "lovastatin", "atorvastatin", "felodipine", "nifedipine",
                             "nisoldipine", "amiodarone", "dronedarone", "cyclosporine", "ciclosporin",
                             "tacrolimus", "sirolimus", "everolimus", "buspirone", "fexofenadine",
                             "ticagrelor", "ivabradine", "colchicine", "grapefruit interaction"),
                   "why": "Grapefruit can raise or lower blood levels of these medicines (FDA consumer update).",
                   "draft_rule": "Grapefruit is excluded from this draft."},
    "enteral": {"label": "Enteral (tube) or parenteral nutrition", "withhold": True,
                "terms": ("enteral", "tube feed", "tube-feed", "peg tube", "peg-j", "ng tube", "nasogastric",
                          "gastrostomy", "jejunostomy", "parenteral", "tpn"),
                "why": "Feeding regimens are prescribed and monitored by the clinical team.",
                "draft_rule": "No food plan."},
    "paediatric": {"label": "Under 19 years old", "withhold": False, "terms": (),
                   "why": "Children and teenagers grow; their needs are assessed by a clinician or dietitian.",
                   "draft_rule": "Adult reference targets are shown only for the clinician to replace."},
}

# Allergy / intolerance words -> food allergen tags (FALCPA + FASTER Act 9 major + gluten)
ALLERGEN_SYNONYMS = {
    "milk": ("milk", "dairy", "lactose", "casein", "whey", "cow's milk", "cows milk", "cheese", "yogurt",
             "yoghurt", "butter"),
    "egg": ("egg", "eggs", "albumin"),
    "fish": ("fish", "salmon", "tuna", "cod", "sardine", "sardines", "tilapia"),
    "shellfish": ("shellfish", "crustacean", "crustaceans", "shrimp", "prawn", "prawns", "crab", "lobster",
                  "mollusc", "mollusk"),
    "tree_nut": ("tree nut", "tree nuts", "tree_nut", "nut", "nuts", "almond", "almonds", "walnut", "walnuts",
                 "cashew", "cashews"),
    "peanut": ("peanut", "peanuts", "groundnut", "groundnuts"),
    "wheat": ("wheat",),
    "soy": ("soy", "soya", "soybean", "soybeans", "tofu", "edamame"),
    "sesame": ("sesame", "tahini"),
    "gluten": ("gluten", "coeliac", "celiac", "barley", "rye"),
}
# tags that a matched allergen class also excludes (precautionary)
ALLERGEN_ALSO = {"gluten": ("gluten", "gluten_may_contain", "wheat"), "wheat": ("wheat",)}

CUISINES = {   # rough starting points only; the person's own foods always win
    "south_asian": ("lentils", "chickpeas", "pigeon_peas", "mung_beans", "white_rice", "spinach", "yogurt",
                    "cauliflower", "okra", "mango"),
    "west_african": ("yam", "plantain", "black_eyed_peas", "okra", "millet", "amaranth_leaves", "peanuts",
                     "tomato", "sardines", "papaya"),
    "east_african": ("teff", "lentils", "collards", "kidney_beans", "banana", "white_rice", "cabbage"),
    "east_asian": ("tofu", "bok_choy", "white_rice", "edamame", "mung_beans", "mushrooms", "salmon"),
    "latin_american": ("corn_tortilla", "black_beans", "plantain", "avocado", "white_rice", "tomato", "guava",
                       "red_pepper"),
    "mediterranean": ("chickpeas", "olive_oil", "bulgur", "lentils", "tomato", "sardines", "eggplant",
                      "greek_yogurt", "walnuts"),
    "middle_eastern": ("chickpeas", "hummus", "bulgur", "lentils", "dates", "lamb", "tahini", "eggplant"),
    "north_american": ("oats", "chicken_breast", "potato", "broccoli", "apple", "milk_2pct", "ww_bread"),
}
MEALS = ("breakfast", "lunch", "dinner", "snack")
MEAL_SHARE = {"breakfast": 0.25, "lunch": 0.32, "dinner": 0.33, "snack": 0.10}
MEAT_GROUPS = ("meat", "poultry")
ANIMAL_FLESH = ("meat", "poultry", "fish", "shellfish")
FRUIT_VEG_GROUPS = ("veg", "fruit")   # WHO excludes starchy roots (potato, cassava, yam ...)


class PlannerError(ValueError):
    """The request cannot be planned as given (bad numbers, unknown diet)."""


# ======================================================================================================
# Food data
# ======================================================================================================
_FOODS_LOCK = threading.Lock()
_FOODS_CACHE = {}


def load_foods(path=None):
    """The offline food subset -> (foods list, source dict). Cached per path."""
    path = path or FOOD_FILE
    with _FOODS_LOCK:
        if path not in _FOODS_CACHE:
            with open(path, encoding="utf-8") as h:
                data = json.load(h)
            foods = data["foods"]
            for f in foods:
                kosher = ("treif" if ("pork" in f["flags"] or f["group"] == "shellfish") else
                          "meat" if f["group"] in MEAT_GROUPS else
                          "dairy" if "milk" in f["allergens"] else "pareve")
                f["kosher_class"] = kosher
                f["_text"] = " ".join((f["id"].replace("_", " "), f["name"], f["fdc_description"])).lower()
            _FOODS_CACHE[path] = (foods, data["source"])
        return _FOODS_CACHE[path]


def food_index(path=None):
    return {f["id"]: f for f in load_foods(path)[0]}


# ======================================================================================================
# Consent
# ======================================================================================================
class LocalConsentLedger:
    """Used only when no consent_center module is installed. Nothing is granted until a person grants it.
    Each grant records who recorded it, when, the purpose and the statement shown. Persisted via `store`."""

    def __init__(self, store=None):
        self.store = store
        self._lock = threading.Lock()
        self._mem = {}

    def _all(self):
        if self.store is not None:
            return self.store.load("consent", {})
        return self._mem

    def _save(self, d):
        if self.store is not None:
            self.store.save("consent", d)
        else:
            self._mem = d

    def grant(self, subject, purpose=CONSENT_PURPOSE, recorded_by="", statement=""):
        if not subject:
            raise PlannerError("A subject is needed to record consent.")
        with self._lock:
            d = self._all()
            d.setdefault(str(subject), {})[purpose] = {"granted": True, "at": _now(), "by": recorded_by,
                                                       "statement": statement[:500]}
            self._save(d)

    def revoke(self, subject, purpose=CONSENT_PURPOSE, recorded_by=""):
        with self._lock:
            d = self._all()
            d.setdefault(str(subject), {})[purpose] = {"granted": False, "at": _now(), "by": recorded_by}
            self._save(d)

    def check(self, subject, purpose=CONSENT_PURPOSE):
        if not subject:
            return False
        rec = self._all().get(str(subject), {}).get(purpose)
        return bool(rec and rec.get("granted"))


def _consent_center():
    try:
        return importlib.import_module("consent_center")
    except Exception:
        return None


def _ask_center(cc, subject, purpose):
    """Ask a consent_center: a ConsentCenter instance (is_active), or a module-level check function."""
    fn = getattr(cc, "is_active", None)
    src = "consent_center"
    if not callable(fn):
        fn = next((getattr(cc, n) for n in ("check_consent", "has_consent", "is_granted", "check")
                   if callable(getattr(cc, n, None))), None)
    if fn is None:
        return {"granted": False, "source": "consent_center is installed but not attached to this clinic "
                                            "(no consent can be checked); treated as no"}
    try:
        res = fn(subject, purpose)
        if isinstance(res, dict):
            res = res.get("granted", res.get("active", False))
        return {"granted": bool(subject) and res is True, "source": src}
    except Exception as e:  # noqa: BLE001 - any failure means no consent
        return {"granted": False, "source": "consent_center error (%s); treated as no" % type(e).__name__}


def consent_status(subject, purpose=CONSENT_PURPOSE, ledger=None, center="auto"):
    """-> {"granted": bool, "source": ...}.

    center: a ConsentCenter instance (clinic.consent_center), a module, None (no consent_center: use the local
    ledger) or "auto" (use the consent_center module if it is installed, else the local ledger). Whenever a
    consent_center is involved its answer is final and any error means NOT granted. The local ledger is off
    until someone grants consent."""
    cc = _consent_center() if center == "auto" else center
    if cc is not None:
        return _ask_center(cc, subject, purpose)
    ledger = ledger or LocalConsentLedger()
    return {"granted": bool(subject) and ledger.check(subject, purpose), "source": "local ledger (default off)"}


# ======================================================================================================
# Targets
# ======================================================================================================
class Target:
    __slots__ = ("key", "kind", "lo", "hi", "weight", "primary", "source", "note")

    def __init__(self, key, kind, lo=None, hi=None, weight=1.0, primary=False, source="", note=""):
        self.key, self.kind, self.lo, self.hi = key, kind, lo, hi
        self.weight, self.primary, self.source, self.note = weight, primary, source, note

    def as_dict(self):
        label, unit, _ = NUTRIENT_INFO.get(self.key, (self.key, "", ""))
        return {"key": self.key, "label": label, "unit": unit, "kind": self.kind,
                "min": None if self.lo is None else round(self.lo, 1),
                "max": None if self.hi is None else round(self.hi, 1),
                "primary": self.primary, "source": self.source, "note": self.note}


def eer_2005(sex, age, weight_kg, height_cm, activity="low_active"):
    """IOM 2005 Estimated Energy Requirement, adults 19+ (kcal/day)."""
    pa = {"male": {"sedentary": 1.00, "low_active": 1.11, "active": 1.25, "very_active": 1.48},
          "female": {"sedentary": 1.00, "low_active": 1.12, "active": 1.27, "very_active": 1.45}}
    if sex not in pa or activity not in pa[sex]:
        raise PlannerError("sex must be male/female and activity sedentary/low_active/active/very_active")
    h = height_cm / 100.0
    if sex == "male":
        return 662 - 9.53 * age + pa[sex][activity] * (15.91 * weight_kg + 539.6 * h)
    return 354 - 6.91 * age + pa[sex][activity] * (9.36 * weight_kg + 726 * h)


def _dri(sex, age, preg, lact):
    """Adult (19+) DRI table values used here. sex None -> the higher of the two (general reference)."""
    def pick(m, f):
        return f if sex == "female" else m if sex == "male" else max(m, f)
    old = age is not None and age >= 51
    d = {
        "protein_g": (pick(56, 46), "dri_macro"),
        "fibre_g": (pick(30 if old else 38, 21 if old else 25), "dri_macro"),
        "potassium_mg": (pick(3400, 2600), "dri_na_k"),
        "calcium_mg": (1200 if (age or 0) >= 71 or (sex == "female" and old) else 1000, "dri_ca_d"),
        "iron_mg": (8 if sex == "male" or (sex == "female" and old) else 18, "dri_micro"),
        "magnesium_mg": (pick(400 if (age or 99) <= 30 else 420, 310 if (age or 99) <= 30 else 320),
                         "dri_ca_p_mg"),
        "vitc_mg": (pick(90, 75), "dri_c"),
        "b12_ug": (2.4, "dri_b"),
        "folate_dfe_ug": (400, "dri_b"),
        "vitk_ug": (pick(120, 90), "dri_micro"),
    }
    if preg:
        d.update(protein_g=(71, "dri_macro"), fibre_g=(28, "dri_macro"), potassium_mg=(2900, "dri_na_k"),
                 iron_mg=(27, "dri_micro"), folate_dfe_ug=(600, "dri_b"), vitc_mg=(85, "dri_c"),
                 b12_ug=(2.6, "dri_b"), magnesium_mg=(350 if (age or 99) <= 30 else 360, "dri_ca_p_mg"))
    elif lact:
        d.update(protein_g=(71, "dri_macro"), fibre_g=(29, "dri_macro"), potassium_mg=(2800, "dri_na_k"),
                 iron_mg=(9, "dri_micro"), folate_dfe_ug=(500, "dri_b"), vitc_mg=(120, "dri_c"),
                 b12_ug=(2.8, "dri_b"))
    return d


def build_targets(profile, personalised, flags=(), clinician_targets=None):
    """-> (list[Target], energy_kcal, notes). Without personalisation only the general adult reference is used."""
    notes = []
    p = profile if personalised else {}
    sex = p.get("sex") if p.get("sex") in ("male", "female") else None
    age = p.get("age")
    preg = bool(p.get("pregnant")) or ("pregnancy" in flags and not p.get("lactating"))
    lact = bool(p.get("lactating"))
    energy = None
    if personalised and p.get("energy_kcal"):
        energy = float(p["energy_kcal"])
        notes.append("Energy target entered by the person or clinician.")
    elif personalised and sex and age and p.get("weight_kg") and p.get("height_cm") and age >= 19:
        energy = eer_2005(sex, float(age), float(p["weight_kg"]), float(p["height_cm"]),
                          p.get("activity", "low_active"))
        if preg:
            energy += {1: 0, 2: 340, 3: 452}.get(int(p.get("trimester") or 2), 340)
        elif lact:
            energy += 330
        notes.append("Energy from the IOM 2005 EER equation for your age, sex, height, weight and activity.")
    if energy is None:
        energy = 2000.0
        notes.append("Energy uses the general 2,000 kcal reference (FDA Daily Values). With consent, a target "
                     "based on your age, sex, height, weight and activity can be used.")
    if not 1000 <= energy <= 5000:
        raise PlannerError("Energy target must be between 1000 and 5000 kcal for a general plan.")
    dri = _dri(sex, age if personalised else None, preg, lact)
    protein = dri["protein_g"][0]
    if personalised and p.get("weight_kg"):
        protein = max(protein, (1.1 if (preg or lact) else 0.8) * float(p["weight_kg"]))
    fibre = max(dri["fibre_g"][0] if personalised and sex else 28.0, 0)
    if not (personalised and sex):
        fibre = round(14 * energy / 1000, 1)   # AI basis: 14 g per 1000 kcal (= FDA DV 28 g at 2000 kcal)
    t = [
        Target("energy_kcal", "range", 0.95 * energy, 1.05 * energy, 12, True, "dri_macro" if personalised
               else "fda_dv"),
        Target("protein_g", "min", protein, 0.35 * energy / 4, 6, True, "dri_macro",
               "RDA; upper end is the AMDR 35% of energy"),
        Target("fibre_g", "min", fibre, None, 5, True, "dri_macro", "AI, 14 g per 1000 kcal"),
        Target("sodium_mg", "max", None, float(p.get("sodium_limit_mg") or 2000), 6, True, "who_sodium",
               "WHO < 2000 mg; NASEM CDRR 2300 mg"),
        Target("potassium_mg", "min", dri["potassium_mg"][0], None, 3, True, "dri_na_k", "AI"),
        Target("fat_g", "range", 0.20 * energy / 9, 0.35 * energy / 9, 1.5, False, "dri_macro", "AMDR 20-35%"),
        Target("carb_g", "range", 0.45 * energy / 4, 0.65 * energy / 4, 1.5, False, "dri_macro", "AMDR 45-65%"),
        Target("satfat_g", "max", None, 0.10 * energy / 9, 1.5, False, "who_fat", "< 10% of energy"),
        Target("fruit_veg_g", "min", 400, None, 1.5, False, "who_healthy_diet", ">= 400 g"),
        Target("calcium_mg", "min", dri["calcium_mg"][0], 2500 if (age or 0) < 51 else 2000, 0.8, False,
               "dri_ca_d", "RDA; max is the UL"),
        Target("iron_mg", "min", dri["iron_mg"][0], 45, 0.5, False, "dri_micro", "RDA; max is the UL"),
        Target("magnesium_mg", "min", dri["magnesium_mg"][0], None, 0.5, False, "dri_ca_p_mg", "RDA"),
        Target("vitc_mg", "min", dri["vitc_mg"][0], 2000, 0.5, False, "dri_c", "RDA; max is the UL"),
        Target("b12_ug", "min", dri["b12_ug"][0], None, 0.5, False, "dri_b", "RDA"),
        Target("folate_dfe_ug", "min", dri["folate_dfe_ug"][0], None, 0.5 if not preg else 2, False, "dri_b",
               "RDA"),
    ]
    if "ckd" in flags:
        t = [x for x in t if x.key not in ("potassium_mg", "phosphorus_mg")]
        for x in t:
            if x.key == "protein_g":
                x.lo, x.kind, x.note = None, "max", "minimum removed for kidney disease: set by clinician"
        notes.append(CLINICAL_FLAGS["ckd"]["draft_rule"])
    if clinician_targets:
        by = {x.key: x for x in t}
        for k, v in clinician_targets.items():
            if k not in NUTRIENT_INFO or not isinstance(v, dict):
                raise PlannerError("Unknown clinician target %r" % k)
            x = by.get(k) or Target(k, "min")
            x.lo, x.hi = v.get("min"), v.get("max")
            x.kind = "range" if x.lo is not None and x.hi is not None else "min" if x.lo is not None else "max"
            x.source, x.note, x.weight, x.primary = "clinician", "set by clinician", max(x.weight, 3), True
            by[k] = x
        t = list(by.values())
        notes.append("Clinician-set targets replace the reference values for: %s." % ", ".join(clinician_targets))
    return t, energy, notes


# ======================================================================================================
# Exclusions (hard constraints)
# ======================================================================================================
def _norm(s):
    return re.sub(r"\s+", " ", str(s or "").strip().lower())


def allergy_exclusions(terms, foods):
    """Allergy / intolerance words -> ({food_id: reason}, report). Unknown words are matched against food
    names and descriptions; a word that matches nothing is reported, never silently dropped."""
    out, report = {}, []
    for raw in terms or ():
        term = _norm(raw)
        if not term:
            continue
        # exact allergen word first; else any allergen word inside the phrase ("coeliac disease")
        classes = [c for c, syn in ALLERGEN_SYNONYMS.items() if term in syn or term.rstrip("s") in syn]
        same = [f for f in foods if term in (f["id"].replace("_", " "), _norm(f["name"]))]
        if not classes and same:   # a food named exactly ("peanut butter"): its own allergen tags
            classes = sorted({a for f in same for a in f["allergens"] if a in ALLERGEN_SYNONYMS})
            for f in same:
                out.setdefault(f["id"], "allergy/intolerance: %s" % raw)
        if not classes and not same:
            classes = [c for c, syn in ALLERGEN_SYNONYMS.items()
                       if any(re.search(r"\b%s\b" % re.escape(x), term) for x in syn)]
        if re.search(r"\bnuts?\b", term) and "tree" not in term and "peanut" not in classes:
            classes.append("peanut")   # "nut allergy" usually includes peanuts: be conservative
        tags = set()
        for c in classes:
            tags |= set(ALLERGEN_ALSO.get(c, (c,)))
        hit = []
        for f in foods:
            # a known allergen class is matched by its tags only (so "egg" never removes eggplant and "milk"
            # never removes soy milk); any other word is matched against food names and USDA descriptions
            word = (re.search(r"\b%s" % re.escape(term.rstrip("s")), f["_text"])
                    if not classes and len(term) >= 3 else None)
            if tags & set(f["allergens"]) or word:
                out.setdefault(f["id"], "allergy/intolerance: %s" % raw)
                hit.append(f["id"])
        report.append({"term": raw, "allergen_classes": classes, "excluded_foods": sorted(hit),
                       "recognised": bool(classes or hit),
                       "note": "" if (classes or hit) else
                       "Not recognised and matched no food in the list. Check every food label yourself."})
    return out, report


def diet_exclusions(prefs, foods):
    """Diet / cultural rules -> ({food_id: reason}, notes)."""
    diet = {_norm(d).replace("-", "_").replace(" ", "_") for d in (prefs.get("diet") or ())}
    known = {"vegetarian", "vegan", "pescatarian", "halal", "kosher", "no_pork", "no_beef", "dairy_free",
             "egg_free", "low_cost", ""}
    bad = diet - known
    if bad:
        raise PlannerError("Unknown diet option(s): %s" % ", ".join(sorted(bad)))
    out, notes = {}, []
    for f in foods:
        g, al, fl = f["group"], set(f["allergens"]), set(f["flags"])
        why = None
        if "vegan" in diet and (g in ANIMAL_FLESH or g in ("dairy", "egg") or al & {"milk", "egg"}):
            why = "vegan"
        elif "vegetarian" in diet and g in ANIMAL_FLESH:
            why = "vegetarian"
        elif "pescatarian" in diet and g in MEAT_GROUPS:
            why = "pescatarian"
        elif ("halal" in diet or "no_pork" in diet) and "pork" in fl:
            why = "halal / no pork"
        elif "kosher" in diet and f["kosher_class"] == "treif":
            why = "kosher"
        elif "no_beef" in diet and "beef" in f["id"]:
            why = "no beef"
        elif "dairy_free" in diet and "milk" in al:
            why = "dairy free"
        elif "egg_free" in diet and "egg" in al:
            why = "egg free"
        if why:
            out[f["id"]] = "diet: " + why
    if "halal" in diet:
        notes.append("Halal: pork is excluded. Choose halal-certified (zabiha) meat and poultry, and check labels "
                     "for gelatin and alcohol. Some schools of thought regard shellfish differently; remove "
                     "shrimp if that applies to you.")
    if "kosher" in diet:
        notes.append("Kosher: pork and shellfish are excluded and meat is never placed in the same meal as "
                     "dairy. Choose kosher-certified meat, poultry and cheese (look for a hechsher).")
    if "vegan" in diet:
        notes.append("Vegan: plant foods only. B12 is not reliably available from plants; use a fortified food "
                     "or a supplement (ask a pharmacist or clinician).")
    return out, notes


def clinical_exclusions(flags, foods):
    out = {}
    for f in foods:
        fl = set(f["flags"])
        if "maoi_tyramine" in flags and "tyramine_high" in fl:
            out[f["id"]] = "MAOI: high-tyramine food"
        elif "grapefruit" in flags and "grapefruit" in fl:
            out[f["id"]] = "medicine-grapefruit interaction"
        elif "pregnancy" in flags and "pregnancy_caution" in fl:
            out[f["id"]] = "pregnancy: listeria caution (soft cheese / dry-cured meat)"
    return out


def detect_flags(profile, extra_text=()):
    """Clinical flags from explicit flags, conditions, medicines and age -> {flag: [reasons]}."""
    found = {}
    for fl in profile.get("clinical_flags") or ():
        if fl not in CLINICAL_FLAGS:
            raise PlannerError("Unknown clinical flag %r (known: %s)" % (fl, ", ".join(CLINICAL_FLAGS)))
        found.setdefault(fl, []).append("flag entered")
    texts = [("condition", c) for c in profile.get("conditions") or ()]
    texts += [("medicine", m) for m in profile.get("medications") or ()]
    texts += [("record", t) for t in extra_text]
    for kind, text in texts:
        low = _norm(text)
        for fl, spec in CLINICAL_FLAGS.items():
            if any(term in low for term in spec["terms"]):
                found.setdefault(fl, []).append("%s: %s" % (kind, text))
    if profile.get("pregnant") or profile.get("lactating"):
        found.setdefault("pregnancy", []).append("pregnant/lactating entered")
    if profile.get("enteral"):
        found.setdefault("enteral", []).append("enteral nutrition entered")
    age = profile.get("age")
    if age is not None and float(age) < 19:
        found.setdefault("paediatric", []).append("age %s" % age)
    return found


# ======================================================================================================
# Optimiser
# ======================================================================================================
def _food_vector(f, keys, prices):
    """Contribution of HALF a serving to each key (missing nutrient values count as 0 and are reported)."""
    g = f["serving_g"] / 2.0
    vec = []
    for k in keys:
        if k == "fruit_veg_g":
            vec.append(g if f["group"] in FRUIT_VEG_GROUPS else 0.0)
        elif k == "cost_usd":
            vec.append(prices.get(f["id"], f["price_usd_per_100g_illustrative"]) * g / 100.0)
        else:
            v = f["per_100g"].get(k)
            vec.append((v or 0.0) * g / 100.0)
    return vec


def _penalty(t, v):
    """Squared relative miss, plus a small fixed step for ANY miss of a primary target so the search does not
    settle a few kcal outside a band to save half a serving."""
    p = 0.0
    if t.lo is not None and v < t.lo:
        p += ((t.lo - v) / max(t.lo, 1e-9)) ** 2 + (0.002 if t.primary else 0.0)
    if t.hi is not None and v > t.hi:
        p += ((v - t.hi) / max(t.hi, 1e-9)) ** 2 + (0.002 if t.primary else 0.0)
    return t.weight * p


def optimise_day(cands, targets, prices=None, bonus=None, max_servings=None, avoid_repeat=None,
                 budget=None, max_iter=500):
    """Deterministic constrained local search over half-servings -> {food_id: servings}.

    cands: foods already filtered by every hard constraint. bonus: {food_id: per-serving score change}
    (negative = preferred). avoid_repeat: {food_id: servings yesterday} for variety. budget: USD/day max."""
    prices, bonus, avoid_repeat = prices or {}, bonus or {}, avoid_repeat or {}
    max_servings = max_servings or {}
    tg = list(targets)
    if budget is not None:
        tg.append(Target("cost_usd", "max", None, float(budget), 8, False, "user"))
    keys = [t.key for t in tg]
    ids = [f["id"] for f in cands]
    vec = {f["id"]: _food_vector(f, keys, prices) for f in cands}
    cap = {}
    for f in cands:
        c = 2 if f["group"] in ("fat", "condiment") else 1 if f["serving_g"] <= 15 else 3
        cap[f["id"]] = min(float(max_servings.get(f["id"], c)), c) * 2   # in half-serving units
    units = {i: 0 for i in ids}
    tot = [0.0] * len(tg)

    def food_term(i, u):   # per-food terms for u half-servings
        s = u / 2.0
        return (bonus.get(i, 0.0) * s + 0.004 * s + 0.03 * max(0.0, s - 2) ** 2
                + 0.04 * min(s, avoid_repeat.get(i, 0.0)))

    def score_with(delta_i, sign, base_tot):
        new = base_tot[:]
        v = vec[delta_i]
        for j in range(len(new)):
            new[j] += sign * v[j]
        return sum(_penalty(tg[j], new[j]) for j in range(len(new))), new

    cur = sum(_penalty(tg[j], tot[j]) for j in range(len(tg)))
    for _ in range(max_iter):
        best = (0.0, None)
        for i in ids:
            for sign in (1, -1):
                u = units[i] + sign
                if u < 0 or u > cap[i]:
                    continue
                s, _new = score_with(i, sign, tot)
                d = (s - cur) + food_term(i, u) - food_term(i, units[i])
                if d < best[0] - 1e-12:
                    best = (d, ("one", i, sign))
        if best[1] is None:   # no single move helps: try swaps of half a serving
            used = [i for i in ids if units[i] > 0]
            for a in used:
                s_a, tot_a = score_with(a, -1, tot)
                fa = food_term(a, units[a] - 1) - food_term(a, units[a])
                for b in ids:
                    if b == a or units[b] + 1 > cap[b]:
                        continue
                    s_b, _n = score_with(b, 1, tot_a)
                    d = (s_b - cur) + fa + food_term(b, units[b] + 1) - food_term(b, units[b])
                    if d < best[0] - 1e-12:
                        best = (d, ("swap", a, b))
        if best[1] is None:
            break
        mv = best[1]
        if mv[0] == "one":
            cur, tot = score_with(mv[1], mv[2], tot)
            units[mv[1]] += mv[2]
        else:
            _s, tot = score_with(mv[1], -1, tot)
            cur, tot = score_with(mv[2], 1, tot)
            units[mv[1]] -= 1
            units[mv[2]] += 1
    return {i: u / 2.0 for i, u in units.items() if u > 0}


def day_totals(servings, foods_by_id, prices=None):
    keys = [k for k in NUTRIENT_INFO]
    tot = dict.fromkeys(keys, 0.0)
    missing = {}
    for fid, s in servings.items():
        f = foods_by_id[fid]
        vec = _food_vector(f, keys, prices or {})
        for k, v in zip(keys, vec):
            tot[k] += v * 2 * s
        for k, v in f["per_100g"].items():
            if v is None:
                missing.setdefault(k, []).append(f["name"])
    return {k: round(v, 1) for k, v in tot.items()}, missing


def check_targets(totals, targets):
    out = []
    for t in targets:
        v = totals.get(t.key, 0.0)
        ok = (t.lo is None or v >= t.lo - 1e-6) and (t.hi is None or v <= t.hi + 1e-6)
        out.append(dict(t.as_dict(), value=round(v, 1), met=ok))
    return out


def assign_meals(servings, foods_by_id, kosher=False):
    """Split a day's servings into meals (whole servings where possible). Kosher: meat and dairy never share
    a meal. -> {meal: [{food, servings}]}."""
    units = []
    for fid, s in sorted(servings.items()):
        whole, rest = int(s), s - int(s)
        units += [(fid, 1.0)] * whole + ([(fid, rest)] if rest else [])
    energy = {m: 0.0 for m in MEALS}
    total = sum(foods_by_id[f]["per_100g"]["energy_kcal"] * foods_by_id[f]["serving_g"] / 100 * s
                for f, s in units) or 1.0
    meals = {m: {} for m in MEALS}
    kinds = {m: set() for m in MEALS}

    def kcal(fid, s):
        f = foods_by_id[fid]
        return f["per_100g"]["energy_kcal"] * f["serving_g"] / 100 * s

    for fid, s in sorted(units, key=lambda u: -kcal(*u)):
        f = foods_by_id[fid]
        allowed = ["lunch", "dinner"]
        if "breakfast" in f["flags"]:
            allowed.append("breakfast")
        if "snack" in f["flags"]:
            allowed.append("snack")
        if f["group"] in ("fat", "condiment", "seed") and "breakfast" not in f["flags"]:
            allowed = ["lunch", "dinner"]
        if kosher:
            kc = f["kosher_class"]
            allowed = [m for m in allowed if not ((kc == "meat" and "dairy" in kinds[m]) or
                                                  (kc == "dairy" and "meat" in kinds[m]))]
            if kc == "meat":
                allowed = [m for m in allowed if m in ("lunch", "dinner")] or allowed
            if not allowed:   # every allowed meal conflicts: use any meal without the conflict
                allowed = [m for m in MEALS if not ((kc == "meat" and "dairy" in kinds[m]) or
                                                    (kc == "dairy" and "meat" in kinds[m]))]
        m = max(allowed, key=lambda m: MEAL_SHARE[m] - energy[m] / total)
        meals[m][fid] = meals[m].get(fid, 0.0) + s
        energy[m] += kcal(fid, s)
        kinds[m].add(f["kosher_class"])
    return {m: [{"food": fid, "servings": s} for fid, s in sorted(items.items())] for m, items in meals.items()}


def kosher_meals_ok(meals, foods_by_id):
    for items in meals.values():
        kc = {foods_by_id[i["food"]]["kosher_class"] for i in items}
        if "meat" in kc and "dairy" in kc:
            return False
    return True


# ======================================================================================================
# Planning
# ======================================================================================================
def _now():
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


def _sha(obj):
    return hashlib.sha256(json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)
                          .encode("utf-8")).hexdigest()


def _item_text(f, s):
    grams = round(f["serving_g"] * s)
    return "%s - %s x %s (%d g)" % (f["name"], ("%g" % s), f["serving_label"], grams)


def make_plan(request, consent=None, foods_path=None, chart=None):
    """Build a plan (or a draft, or a withheld result) from a request dict.

    request keys (all optional): subject (id used for consent), days (1-14), profile {age, sex, weight_kg,
    height_cm, activity, pregnant, lactating, trimester, energy_kcal, sodium_limit_mg, conditions[],
    medications[], clinical_flags[], enteral}, allergies[], intolerances[], diet[], avoid[], liked[],
    disliked[], local_foods[], cuisine, budget_per_day_usd, prices {food_id: USD per 100 g},
    clinician_targets {key: {min, max}}.
    consent: result of consent_status() (None -> asked now with the default ledger).
    chart: optional clinic chart view (used only with consent) for allergies / medicines / problems."""
    if not isinstance(request, dict):
        raise PlannerError("request must be an object")
    foods, source = load_foods(foods_path)
    by_id = {f["id"]: f for f in foods}
    subject = request.get("subject")
    consent = consent if consent is not None else consent_status(subject)
    personalised = bool(consent.get("granted"))
    profile = dict(request.get("profile") or {})
    try:
        days = 1 if request.get("days") in (None, "") else int(request.get("days"))
    except (TypeError, ValueError):
        raise PlannerError("days must be a whole number") from None
    if not 1 <= days <= 14:
        raise PlannerError("days must be 1-14")
    for k in ("age", "weight_kg", "height_cm", "energy_kcal", "sodium_limit_mg"):
        if profile.get(k) is not None:
            try:
                profile[k] = float(profile[k])
            except (TypeError, ValueError):
                raise PlannerError("%s must be a number" % k) from None
            if profile[k] <= 0 or not math.isfinite(profile[k]):
                raise PlannerError("%s must be positive" % k)
    allergies = list(request.get("allergies") or ()) + list(request.get("intolerances") or ())
    chart_text = []
    chart_used = False
    if chart and personalised:
        chart_used = True
        for a in chart.get("allergies") or ():
            if a.get("status", "active") == "active" and a.get("substance"):
                allergies.append(a["substance"])
        for m in chart.get("medications") or ():
            if m.get("status", "active") == "active" and m.get("name"):
                chart_text.append(m["name"])
        for p in chart.get("problems") or ():
            if p.get("status", "active") == "active" and p.get("description"):
                chart_text.append(p["description"])
    flags = detect_flags(profile, chart_text)
    result = {"notice": NOTICE, "module": "nutrition_planner", "version": MODULE_VERSION, "created": _now(),
              "personalised": personalised, "consent": consent, "chart_used": chart_used,
              "clinical_flags": [dict(flag=k, label=CLINICAL_FLAGS[k]["label"], reasons=v,
                                      why=CLINICAL_FLAGS[k]["why"], draft_rule=CLINICAL_FLAGS[k]["draft_rule"])
                                 for k, v in sorted(flags.items())],
              "requires_clinician_signoff": bool(flags), "signed_off": None,
              "review_note": REVIEW_NOTE if flags else "", "sources": SOURCES,
              "food_data": source}
    if not personalised and any(profile.get(k) is not None for k in ("age", "sex", "weight_kg", "height_cm",
                                                                     "energy_kcal")):
        result["personalisation_note"] = ("Personal details were not used because consent for personalised "
                                          "nutrition has not been given. General reference targets are shown.")
    if any(CLINICAL_FLAGS[f]["withhold"] for f in flags):
        result.update(status="withheld_clinician_review", review_note=REVIEW_NOTE + " " + WITHHELD_NOTE,
                      days=[], shopping_list=[], targets=[],
                      explanations=["%s: %s" % (CLINICAL_FLAGS[f]["label"], CLINICAL_FLAGS[f]["why"])
                                    for f in sorted(flags)])
        result["plan_sha256"] = _sha({k: v for k, v in result.items() if k != "created"})
        return result
    targets, energy, tnotes = build_targets(profile, personalised, flags, request.get("clinician_targets"))
    if request.get("clinician_targets") and not flags:
        result["requires_clinician_signoff"] = True
        result["review_note"] = "Clinician-set targets: sign-off required."
    excluded = {}
    ex_allergy, allergy_report = allergy_exclusions(allergies, foods)
    ex_diet, diet_notes = diet_exclusions(request, foods)
    ex_clin = clinical_exclusions(flags, foods)
    for d in (ex_allergy, ex_diet, ex_clin):
        for k, v in d.items():
            excluded.setdefault(k, v)
    for a in request.get("avoid") or ():
        hits = [f["id"] for f in foods if _norm(a) and (_norm(a) == f["id"] or _norm(a) in f["_text"]
                                                         or _norm(a) == f["group"])]
        for h in hits:
            excluded.setdefault(h, "avoid: %s" % a)
    disliked = {_norm(x) for x in request.get("disliked") or ()}
    for f in foods:
        if f["id"] in disliked or _norm(f["name"]) in disliked:
            excluded.setdefault(f["id"], "disliked")
    cands = [f for f in foods if f["id"] not in excluded]
    if len(cands) < 8:
        raise PlannerError("Too few foods remain after exclusions (%d). Add foods or relax non-safety "
                           "preferences." % len(cands))
    bonus = {}
    cuisine = request.get("cuisine")
    if cuisine:
        if cuisine not in CUISINES:
            raise PlannerError("Unknown cuisine %r (known: %s)" % (cuisine, ", ".join(CUISINES)))
        for fid in CUISINES[cuisine]:
            bonus[fid] = bonus.get(fid, 0.0) - 0.01
    for x in list(request.get("liked") or ()) + list(request.get("local_foods") or ()):
        for f in cands:
            if _norm(x) in (f["id"], _norm(f["name"])) or (len(_norm(x)) >= 4 and _norm(x) in f["_text"]):
                bonus[f["id"]] = bonus.get(f["id"], 0.0) - 0.01
    for fid, w in (request.get("_learned_bonus") or {}).items():   # set only by NutritionService under consent
        bonus[fid] = bonus.get(fid, 0.0) + float(w)
    prices = {k: float(v) for k, v in (request.get("prices") or {}).items()}
    budget = request.get("budget_per_day_usd")
    budget = float(budget) if budget not in (None, "") else None
    kosher = "kosher" in {_norm(d) for d in request.get("diet") or ()}
    plan_days, prev, vitk_day1 = [], {}, None
    agg = {}
    for d in range(days):
        tg = list(targets)
        if "warfarin_vitk" in flags and vitk_day1 is not None:
            tg.append(Target("vitk_ug", "range", 0.8 * vitk_day1, 1.2 * vitk_day1 if vitk_day1 else 1, 6,
                             True, "clinician", "kept steady: day 1 +/- 20% (warfarin)"))
        serv = optimise_day(cands, tg, prices=prices, bonus=bonus, avoid_repeat=prev, budget=budget)
        totals, missing = day_totals(serv, by_id, prices)
        if "warfarin_vitk" in flags and vitk_day1 is None:
            vitk_day1 = totals["vitk_ug"]
        meals = assign_meals(serv, by_id, kosher=kosher)
        for fid, s in serv.items():
            agg[fid] = agg.get(fid, 0.0) + s
        checks = check_targets(totals, tg)
        meal_view = {}
        for m, items in meals.items():
            rows = []
            for it in items:
                f = by_id[it["food"]]
                carbs = (f["per_100g"]["carb_g"] or 0) * f["serving_g"] / 100 * it["servings"]
                rows.append({"food": f["id"], "name": f["name"], "servings": it["servings"],
                             "grams": round(f["serving_g"] * it["servings"]), "serving_label": f["serving_label"],
                             "text": _item_text(f, it["servings"]), "carb_g": round(carbs, 1)})
            meal_view[m] = rows
        day = {"day": d + 1, "meals": meal_view, "totals": totals, "checks": checks,
               "primary_targets_met": all(c["met"] for c in checks if c["primary"]),
               "not_reported": {k: sorted(set(v)) for k, v in missing.items()}}
        if "diabetes" in flags:
            day["carb_g_per_meal"] = {m: round(sum(r["carb_g"] for r in rows), 1) for m, rows in meal_view.items()}
        if kosher:
            day["kosher_meat_dairy_separate"] = kosher_meals_ok(meals, by_id)
        plan_days.append(day)
        prev = {k: v * 0.6 for k, v in prev.items()}   # decaying history: variety across the whole plan
        for k, v in serv.items():
            prev[k] = prev.get(k, 0.0) + v
    status = "draft_requires_clinician_signoff" if result["requires_clinician_signoff"] else "general_information"
    result.update(status=status, energy_target_kcal=round(energy), targets=[t.as_dict() for t in targets],
                  target_notes=tnotes, days=plan_days, excluded=dict(sorted(excluded.items())),
                  allergy_report=allergy_report, diet_notes=diet_notes,
                  shopping_list=shopping_list(agg, by_id, prices),
                  price_note=("Prices are your own." if prices else
                              "Prices are rough illustrative placeholders (USD), not market data."),
                  budget_per_day_usd=budget)
    result["explanations"] = explain(result, targets, by_id)
    # last line of defence: nothing excluded may appear anywhere in the plan
    for day in plan_days:
        for rows in day["meals"].values():
            for r in rows:
                if r["food"] in excluded:
                    raise AssertionError("excluded food %s reached the plan" % r["food"])
    result["plan_sha256"] = _sha({k: v for k, v in result.items() if k not in ("created", "plan_sha256")})
    return result


def shopping_list(agg, by_id, prices=None):
    prices = prices or {}
    rows = []
    for fid, s in agg.items():
        f = by_id[fid]
        grams = f["serving_g"] * s
        price = prices.get(fid, f["price_usd_per_100g_illustrative"])
        rows.append({"food": fid, "name": f["name"], "section": f["group"], "servings": s,
                     "grams": round(grams), "serving_label": f["serving_label"],
                     "estimated_cost_usd": round(price * grams / 100, 2),
                     "text": "%s: about %d g (%g x %s)" % (f["name"], round(grams), s, f["serving_label"])})
    rows.sort(key=lambda r: (r["section"], r["name"]))
    return rows


def explain(plan, targets, by_id):
    """Plain-language lines for the plan."""
    out = []
    if plan["status"] == "draft_requires_clinician_signoff":
        out.append(REVIEW_NOTE)
    out.append("How this plan was made: foods are picked one half-serving at a time, keeping whatever brings "
               "the day closest to every target, while never using a food you cannot or will not eat.")
    if not plan["personalised"]:
        out.append("These are general adult reference targets. They are not tailored to you.")
    d1 = plan["days"][0] if plan["days"] else None
    if d1:
        for c in d1["checks"]:
            if not c["primary"] and c["met"]:
                continue
            label, unit, why = NUTRIENT_INFO.get(c["key"], (c["key"], "", ""))
            if c["kind"] == "max":
                goal = "at most %s %s" % (c["max"], unit)
            elif c["kind"] == "range" or (c["min"] is not None and c["max"] is not None and c["key"] == "energy_kcal"):
                goal = "between %s and %s %s" % (c["min"], c["max"], unit)
            else:
                goal = "at least %s %s" % (c["min"], unit)
            top = _top_sources(d1, c["key"], by_id)
            out.append("%s: %s %s on day 1 (aim %s) - %s. %s%s" % (
                label, c["value"], unit, goal, "on target" if c["met"] else "not reached with these foods",
                why, (" Main sources: %s." % ", ".join(top)) if top else ""))
    for f in plan.get("clinical_flags") or ():
        out.append("%s: %s %s" % (f["label"], f["why"], f["draft_rule"]))
    for n in plan.get("diet_notes") or ():
        out.append(n)
    for a in plan.get("allergy_report") or ():
        if a["note"]:
            out.append("Allergy '%s': %s" % (a["term"], a["note"]))
    if plan.get("excluded"):
        out.append("%d foods were left out because of allergies, diet or safety rules; they can never be "
                   "added by the optimiser." % len(plan["excluded"]))
    return out


def _top_sources(day, key, by_id, n=3):
    contrib = {}
    for rows in day["meals"].values():
        for r in rows:
            f = by_id[r["food"]]
            v = f["per_100g"].get(key) if key in f["per_100g"] else None
            if key == "fruit_veg_g":
                v = 100.0 if f["group"] in FRUIT_VEG_GROUPS else 0.0
            if v:
                contrib[f["name"]] = contrib.get(f["name"], 0.0) + v * r["grams"] / 100
    return [k for k, _ in sorted(contrib.items(), key=lambda kv: -kv[1])[:n]]


def sign_off(plan, clinician, verified):
    """A clinician approves a DRAFT. `verified` must be True only after the caller checked the clinician's
    identity (clinic_app: Clinic.verify_pin). Withheld results cannot be signed (there is nothing to sign).
    -> a new plan dict with status clinician_approved. The plan hash must still match its content."""
    if plan.get("status") == "withheld_clinician_review":
        raise PlannerError("There is no plan to sign: this situation needs a clinician-made plan.")
    if plan.get("status") not in ("draft_requires_clinician_signoff", "general_information"):
        raise PlannerError("Only a draft can be signed.")
    if verified is not True:
        raise PlannerError("Clinician identity was not verified; nothing was signed.")
    body = {k: v for k, v in plan.items() if k not in ("created", "plan_sha256")}
    if _sha(body) != plan.get("plan_sha256"):
        raise PlannerError("The plan was changed after it was made; make a new draft.")
    out = copy.deepcopy(plan)
    out["status"] = "clinician_approved"
    out["signed_off"] = {"by": clinician.get("id"), "name": clinician.get("display") or clinician.get("username"),
                         "at": _now(), "plan_sha256": plan["plan_sha256"]}
    out["notice"] = NOTICE
    return out


def plan_text(plan):
    """Printable text version (always starts with the notice)."""
    lines = ["Stark Covenant - nutrition plan", NOTICE, ""]
    if plan.get("review_note"):
        lines += [plan["review_note"], ""]
    lines.append("Status: %s" % plan["status"].replace("_", " "))
    for d in plan.get("days") or ():
        lines.append("")
        lines.append("Day %d" % d["day"])
        for m in MEALS:
            rows = d["meals"].get(m) or []
            if rows:
                lines.append("  %s:" % m.capitalize())
                lines += ["    - " + r["text"] for r in rows]
        t = d["totals"]
        lines.append("  Totals: %d kcal, protein %s g, fibre %s g, sodium %s mg, potassium %s mg" % (
            t["energy_kcal"], t["protein_g"], t["fibre_g"], t["sodium_mg"], t["potassium_mg"]))
    if plan.get("shopping_list"):
        lines += ["", "Shopping list"] + ["  - " + r["text"] for r in plan["shopping_list"]]
    if plan.get("explanations"):
        lines += ["", "Explanations"] + ["  * " + e for e in plan["explanations"]]
    lines += ["", NOTICE]
    return "\n".join(lines)


def export_pdf(plan):
    """PDF bytes via polyglot_engine (None if it is not installed)."""
    try:
        pe = importlib.import_module("polyglot_engine")
    except Exception:
        return None
    pdf = pe.PolyglotPdf("Nutrition plan", author=APP_NAME, subject="General nutrition information")
    pdf.heading("Nutrition plan")
    for para in plan_text(plan).split("\n"):
        if para.strip():
            pdf.paragraph(para)
    return pdf.build()


# ======================================================================================================
# Service + consent-gated storage + clinic_app mount
# ======================================================================================================
class JsonStore:
    """Small JSON documents, atomically written; encrypted with `vault` (anything with encrypt/decrypt)
    when given. The document name is sealed inside so files cannot be swapped."""

    def __init__(self, root, vault=None):
        self.root, self.vault = root, vault
        self._lock = threading.Lock()

    def _path(self, name):
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,40}", name):
            raise ValueError("bad store name")
        return os.path.join(self.root, name + (".bin" if self.vault is not None else ".json"))

    def load(self, name, default):
        p = self._path(name)
        try:
            with open(p, "rb") as h:
                raw = h.read()
        except FileNotFoundError:
            return copy.deepcopy(default)
        if self.vault is not None:
            raw = self.vault.decrypt(raw)
        doc = json.loads(raw.decode("utf-8"))
        if doc.get("_name") != name:
            raise ValueError("store document does not belong here")
        return doc["data"]

    def save(self, name, data):
        os.makedirs(self.root, exist_ok=True)
        raw = json.dumps({"_name": name, "data": data}, sort_keys=True).encode("utf-8")
        if self.vault is not None:
            raw = self.vault.encrypt(raw)
        p = self._path(name)
        tmp = p + ".tmp"
        with self._lock:
            with open(tmp, "wb") as h:
                h.write(raw)
                h.flush()
                os.fsync(h.fileno())
            os.replace(tmp, p)


class NutritionService:
    """Planner + consent + remembered likes/dislikes (only under consent).

    center: see consent_status(); center_provider: a callable returning it at call time (clinic_app's
    consent_center may be mounted after this module). With a ConsentCenter instance, remembered preferences
    live in consent_center's own "nutrition" purpose data (data-minimised fields, deleted on withdrawal);
    otherwise in `store` (only while the local ledger says yes)."""

    LIST_SEP = ", "

    def __init__(self, store=None, center="auto", center_provider=None):
        self.store = store
        self._center = center
        self._provider = center_provider
        self.ledger = LocalConsentLedger(store)

    @property
    def center(self):
        if self._provider is not None:
            try:
                return self._provider()
            except Exception:
                return "auto"
        return self._center

    def _cc_instance(self):
        cc = self.center
        return cc if callable(getattr(cc, "is_active", None)) and callable(getattr(cc, "read", None)) else None

    def consent(self, subject):
        return consent_status(subject, CONSENT_PURPOSE, self.ledger, self.center)

    def plan(self, request, chart=None):
        req = {k: v for k, v in dict(request or {}).items() if not str(k).startswith("_")}
        cons = self.consent(req.get("subject"))
        if cons["granted"]:
            learned = self.learned(req.get("subject"))
            req["_learned_bonus"] = learned["bonus"]
            if learned["diet"] and not req.get("diet"):
                req["diet"] = learned["diet"]
        return make_plan(req, consent=cons, chart=chart)

    def _prefs(self, subject):
        """-> {"liked": set, "disliked": set, "diet": [...]} (empty without consent)."""
        out = {"liked": set(), "disliked": set(), "diet": []}
        cc = self._cc_instance()
        if cc is not None:
            try:
                d = cc.read(subject, CONSENT_PURPOSE, reader="nutrition_planner")
            except Exception:
                return out

            def split(v):
                return {x.strip() for x in str(v or "").split(",") if x.strip()}
            out["liked"], out["disliked"] = split(d.get("food_preferences")), split(d.get("food_dislikes"))
            words = _norm(" ".join(str(d.get(k) or "") for k in ("dietary_pattern",
                                                                   "cultural_or_religious_practices")))
            out["diet"] = [x for x in ("vegan", "vegetarian", "pescatarian", "halal", "kosher") if x in words]
            if "vegan" in out["diet"] and "vegetarian" in out["diet"]:
                out["diet"].remove("vegetarian")
            return out
        if self.store is None:
            return out
        fb = self.store.load("feedback", {}).get(str(subject), {})
        out["liked"] = {f for f, v in fb.items() if v}
        out["disliked"] = {f for f, v in fb.items() if not v}
        return out

    def learned(self, subject):
        p = self._prefs(subject)
        idx = food_index()
        bonus = {f: -0.03 for f in p["liked"] if f in idx}
        bonus.update({f: 0.06 for f in p["disliked"] if f in idx})
        return {"bonus": bonus, "diet": p["diet"]}

    def feedback(self, subject, food, liked, actor=None):
        """Remember a like / dislike to adapt later plans. Refused without consent (nothing stored)."""
        if not self.consent(subject)["granted"]:
            return {"stored": False, "reason": "No consent for personalised nutrition; nothing was stored.",
                    "notice": NOTICE}
        if food not in food_index():
            raise PlannerError("Unknown food %r" % food)
        cc = self._cc_instance()
        if cc is not None:
            if actor is None:
                return {"stored": False, "reason": "A signed-in staff member must record this.", "notice": NOTICE}
            p = self._prefs(subject)
            (p["liked"] if liked else p["disliked"]).add(food)
            (p["disliked"] if liked else p["liked"]).discard(food)
            data = {"food_preferences": self.LIST_SEP.join(sorted(p["liked"]))[:300],
                    "food_dislikes": self.LIST_SEP.join(sorted(p["disliked"]))[:200]}
            cc.write(actor, subject, CONSENT_PURPOSE, data)
            return {"stored": True, "where": "consent_center", "notice": NOTICE}
        if self.store is None:
            return {"stored": False, "reason": "No storage configured.", "notice": NOTICE}
        fb = self.store.load("feedback", {})
        fb.setdefault(str(subject), {})[food] = bool(liked)
        self.store.save("feedback", fb)
        return {"stored": True, "where": "local store", "notice": NOTICE}

    def forget(self, subject):
        """Delete everything this service holds locally for a person (consent withdrawn / erasure)."""
        if self.store is None:
            return
        for name in ("feedback", "consent"):
            d = self.store.load(name, {})
            if d.pop(str(subject), None) is not None:
                self.store.save(name, d)


def _host_module(app):
    mod = sys.modules.get(type(app).__module__) if app is not None else None
    if mod is None or not hasattr(mod, "ROUTES"):
        mod = sys.modules.get("clinic_app") or importlib.import_module("clinic_app")
    return mod


def _clinic_of(app):
    return getattr(app, "clinic", app)


def _service_for(app, holder):
    """Data dir follows the clinic's vault like emergency_comms: <data>/nutrition (encrypted) or
    <data>/nutrition_no_vault. Rebuilt when the vault state changes."""
    clinic = _clinic_of(app)
    vault = getattr(clinic, "vault", None)
    key = id(vault)
    if holder.get("key") != key or "svc" not in holder:
        root = os.path.join(clinic.data_root, "nutrition" if vault is not None else "nutrition_no_vault")
        holder["svc"] = NutritionService(JsonStore(root, vault), center_provider=lambda: _center_of(clinic))
        holder["key"] = key
    return holder["svc"]


def _center_of(clinic):
    """clinic.consent_center when consent_center is mounted; "auto" otherwise (installed but unattached means
    no consent can be given yet; not installed means the local ledger)."""
    cc = getattr(clinic, "consent_center", None)
    return cc if cc is not None else "auto"


def mount(app, routes=None):
    """Register /api/nutrition/* in clinic_app's ROUTES (see module docstring). Returns the route table used.
    `app` is a CovenantApp (or a Clinic). Never raises for a missing optional piece."""
    host = _host_module(app)
    routes = host.ROUTES if routes is None else routes
    HttpError = getattr(host, "HttpError", PlannerError)
    holder = {}

    def guard(fn):
        def run(c, u, q, b):
            try:
                return fn(c, u, q, b)
            except PlannerError as e:
                raise HttpError(400, str(e)) from None
        return run

    def r_info(c, u, q, b):
        foods, src = load_foods()
        return {"notice": NOTICE, "sources": SOURCES, "food_data": src, "cuisines": sorted(CUISINES),
                "clinical_flags": {k: v["label"] for k, v in CLINICAL_FLAGS.items()},
                "foods": [{"id": f["id"], "name": f["name"], "group": f["group"], "allergens": f["allergens"]}
                          for f in foods]}

    def r_plan(c, u, q, b):
        req = b.get("request") or {}
        pid = b.get("patient")
        chart = None
        if pid:
            req.setdefault("subject", pid)
            if req["subject"] != pid:
                raise HttpError(400, "subject must be the patient id")
            svc = _service_for(app, holder)
            if svc.consent(pid)["granted"]:
                c.require(u, "clin.read")
                chart = c.view(u, pid)
        plan = _service_for(app, holder).plan(req, chart=chart)
        return {"plan": plan}

    def r_signoff(c, u, q, b):
        c.require(u, "note.sign")
        ok = c.verify_pin(u, b.get("pin"))
        return {"plan": sign_off(b.get("plan") or {}, u, ok is True)}

    def r_consent(c, u, q, b):
        c.require(u, "demo.write")
        svc = _service_for(app, holder)
        if _center_of(_clinic_of(app)) != "auto" or _consent_center() is not None:
            raise HttpError(409, "Consent is managed by consent_center (/api/consent/...).")
        if b.get("grant"):
            svc.ledger.grant(b.get("subject"), CONSENT_PURPOSE, u.get("id", ""), str(b.get("statement") or ""))
        else:
            svc.ledger.revoke(b.get("subject"), CONSENT_PURPOSE, u.get("id", ""))
        return {"consent": svc.consent(b.get("subject")), "notice": NOTICE}

    def r_feedback(c, u, q, b):
        c.require(u, "demo.write")
        return _service_for(app, holder).feedback(b.get("subject"), b.get("food"), bool(b.get("liked")), actor=u)

    routes[("GET", "/api/nutrition/info")] = guard(r_info)
    routes[("POST", "/api/nutrition/plan")] = guard(r_plan)
    routes[("POST", "/api/nutrition/signoff")] = guard(r_signoff)
    routes[("POST", "/api/nutrition/consent")] = guard(r_consent)
    routes[("POST", "/api/nutrition/feedback")] = guard(r_feedback)
    cc = getattr(_clinic_of(app), "consent_center", None)
    if cc is not None and callable(getattr(cc, "on_withdraw", None)):
        def _withdrawn(sid, key, actor):   # consent_center deletes its own purpose data; drop any local copy
            if key == CONSENT_PURPOSE:
                _service_for(app, holder).forget(sid)
        cc.on_withdraw(_withdrawn)
        if callable(getattr(cc, "register_holder", None)):   # subject-access export + erasure reach us too
            def _export(sid):
                svc = _service_for(app, holder)
                return {"local_feedback": svc.store.load("feedback", {}).get(str(sid)) if svc.store else None}

            def _erase(sid, actor):
                _service_for(app, holder).forget(sid)
                return "removed"
            cc.register_holder("nutrition_planner", export=_export, erase=_erase)
    return routes


if __name__ == "__main__":   # quick demo: general plan for today
    print(plan_text(make_plan({"days": 1, "diet": ["vegetarian"], "allergies": ["peanut"]})))
