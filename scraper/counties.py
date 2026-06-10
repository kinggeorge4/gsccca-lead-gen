"""
All 159 Georgia counties with their GSCCCA Premium search numeric IDs.
IDs sourced directly from the live ctl00$BodyContent$ddlCounties dropdown.
"""

# County name → numeric dropdown ID (verified from live Premium search form)
COUNTY_IDS: dict[str, str] = {
    "APPLING": "1", "ATKINSON": "2", "BACON": "3", "BAKER": "4",
    "BALDWIN": "5", "BANKS": "6", "BARROW": "7", "BARTOW": "8",
    "BEN HILL": "9", "BERRIEN": "10", "BIBB": "11", "BLECKLEY": "12",
    "BRANTLEY": "13", "BROOKS": "14", "BRYAN": "15", "BULLOCH": "16",
    "BURKE": "17", "BUTTS": "18", "CALHOUN": "19", "CAMDEN": "20",
    "CANDLER": "21", "CARROLL": "22", "CATOOSA": "23", "CHARLTON": "24",
    "CHATHAM": "25", "CHATTAHOOCHEE": "26", "CHATTOOGA": "27", "CHEROKEE": "28",
    "CLARKE": "29", "CLAY": "30", "CLAYTON": "31", "CLINCH": "32",
    "COBB": "33", "COFFEE": "34", "COLQUITT": "35", "COLUMBIA": "36",
    "COOK": "37", "COWETA": "38", "CRAWFORD": "39", "CRISP": "40",
    "DADE": "41", "DAWSON": "42", "DECATUR": "43", "DEKALB": "44",
    "DODGE": "45", "DOOLY": "46", "DOUGHERTY": "47", "DOUGLAS": "48",
    "EARLY": "49", "ECHOLS": "50", "EFFINGHAM": "51", "ELBERT": "52",
    "EMANUEL": "53", "EVANS": "54", "FANNIN": "55", "FAYETTE": "56",
    "FLOYD": "57", "FORSYTH": "58", "FRANKLIN": "59", "FULTON": "60",
    "GILMER": "61", "GLASCOCK": "62", "GLYNN": "63", "GORDON": "64",
    "GRADY": "65", "GREENE": "66", "GWINNETT": "67", "HABERSHAM": "68",
    "HALL": "69", "HANCOCK": "70", "HARALSON": "71", "HARRIS": "72",
    "HART": "73", "HEARD": "74", "HENRY": "75", "HOUSTON": "76",
    "IRWIN": "77", "JACKSON": "78", "JASPER": "79", "JEFF DAVIS": "80",
    "JEFFERSON": "81", "JENKINS": "82", "JOHNSON": "83", "JONES": "84",
    "LAMAR": "85", "LANIER": "86", "LAURENS": "87", "LEE": "88",
    "LIBERTY": "89", "LINCOLN": "90", "LONG": "91", "LOWNDES": "92",
    "LUMPKIN": "93", "MACON": "94", "MADISON": "95", "MARION": "96",
    "MCDUFFIE": "97", "MCINTOSH": "98", "MERIWETHER": "99", "MILLER": "100",
    "MITCHELL": "101", "MONROE": "102", "MONTGOMERY": "103", "MORGAN": "104",
    "MURRAY": "105", "MUSCOGEE": "106", "NEWTON": "107", "OCONEE": "108",
    "OGLETHORPE": "109", "PAULDING": "110", "PEACH": "111", "PICKENS": "112",
    "PIERCE": "113", "PIKE": "114", "POLK": "115", "PULASKI": "116",
    "PUTNAM": "117", "QUITMAN": "118", "RABUN": "119", "RANDOLPH": "120",
    "RICHMOND": "121", "ROCKDALE": "122", "SCHLEY": "123", "SCREVEN": "124",
    "SEMINOLE": "125", "SPALDING": "126", "STEPHENS": "127", "STEWART": "128",
    "SUMTER": "129", "TALBOT": "130", "TALIAFERRO": "131", "TATTNALL": "132",
    "TAYLOR": "133", "TELFAIR": "134", "TERRELL": "135", "THOMAS": "136",
    "TIFT": "137", "TOOMBS": "138", "TOWNS": "139", "TREUTLEN": "140",
    "TROUP": "141", "TURNER": "142", "TWIGGS": "143", "UNION": "144",
    "UPSON": "145", "WALKER": "146", "WALTON": "147", "WARE": "148",
    "WARREN": "149", "WASHINGTON": "150", "WAYNE": "151", "WEBSTER": "152",
    "WHEELER": "153", "WHITE": "154", "WHITFIELD": "155", "WILCOX": "156",
    "WILKES": "157", "WILKINSON": "158", "WORTH": "159",
}

GA_COUNTIES: list[str] = list(COUNTY_IDS.keys())

assert len(GA_COUNTIES) == 159, f"Expected 159 counties, got {len(GA_COUNTIES)}"


def resolve_counties(spec: str) -> list[str]:
    """
    Parse the COUNTIES env var / workflow input into a list of county names.

    Accepts:
        "ALL"                    → all 159 counties
        "FULTON,COBB,DEKALB"    → specific counties (comma-separated)
    """
    if spec.strip().upper() == "ALL":
        return GA_COUNTIES.copy()
    return [c.strip().upper() for c in spec.split(",") if c.strip()]
