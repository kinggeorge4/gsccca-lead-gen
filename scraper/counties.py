"""All 159 Georgia counties, uppercase, matching GSCCCA dropdown format."""

GA_COUNTIES: list[str] = [
    "APPLING", "ATKINSON", "BACON", "BAKER", "BALDWIN", "BANKS", "BARROW",
    "BARTOW", "BEN HILL", "BERRIEN", "BIBB", "BLECKLEY", "BRANTLEY", "BROOKS",
    "BRYAN", "BULLOCH", "BURKE", "BUTTS", "CALHOUN", "CAMDEN", "CANDLER",
    "CARROLL", "CATOOSA", "CHARLTON", "CHATHAM", "CHATTAHOOCHEE", "CHATTOOGA",
    "CHEROKEE", "CLARKE", "CLAY", "CLAYTON", "CLINCH", "COBB", "COFFEE",
    "COLQUITT", "COLUMBIA", "COOK", "COWETA", "CRAWFORD", "CRISP", "DADE",
    "DAWSON", "DECATUR", "DEKALB", "DODGE", "DOOLY", "DOUGHERTY", "DOUGLAS",
    "EARLY", "ECHOLS", "EFFINGHAM", "ELBERT", "EMANUEL", "EVANS", "FANNIN",
    "FAYETTE", "FLOYD", "FORSYTH", "FRANKLIN", "FULTON", "GILMER", "GLASCOCK",
    "GLYNN", "GORDON", "GRADY", "GREENE", "GWINNETT", "HABERSHAM", "HALL",
    "HANCOCK", "HARALSON", "HARRIS", "HART", "HEARD", "HENRY", "HOUSTON",
    "IRWIN", "JACKSON", "JASPER", "JEFF DAVIS", "JEFFERSON", "JENKINS",
    "JOHNSON", "JONES", "LAMAR", "LANIER", "LAURENS", "LEE", "LIBERTY",
    "LINCOLN", "LONG", "LOWNDES", "LUMPKIN", "MACON", "MADISON", "MARION",
    "MCDUFFIE", "MCINTOSH", "MERIWETHER", "MILLER", "MITCHELL", "MONROE",
    "MONTGOMERY", "MORGAN", "MURRAY", "MUSCOGEE", "NEWTON", "OCONEE",
    "OGLETHORPE", "PAULDING", "PEACH", "PICKENS", "PIERCE", "PIKE", "POLK",
    "PULASKI", "PUTNAM", "QUITMAN", "RABUN", "RANDOLPH", "RICHMOND", "ROCKDALE",
    "SCHLEY", "SCREVEN", "SEMINOLE", "SPALDING", "STEPHENS", "STEWART",
    "SUMTER", "TALBOT", "TALIAFERRO", "TATTNALL", "TAYLOR", "TELFAIR",
    "TERRELL", "THOMAS", "TIFT", "TOOMBS", "TOWNS", "TREUTLEN", "TROUP",
    "TURNER", "TWIGGS", "UNION", "UPSON", "WALKER", "WALTON", "WARE",
    "WARREN", "WASHINGTON", "WAYNE", "WEBSTER", "WHEELER", "WHITE",
    "WHITFIELD", "WILCOX", "WILKES", "WILKINSON", "WORTH",
]

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
