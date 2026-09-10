"""INTERNAL - heraldic keyword lists imported by data_prep/segment.py; maintained via cli.py discover-heraldic-keywords. Not run directly."""










# ============================================================================
# REVIEW BACKLOG — keywords flagged for in-depth review (2026-05-11)
# ----------------------------------------------------------------------------
# These entries each generated multiple false positives in the 800-product
# hand-labeled validation sample (see validate_heraldic_filter.py and the
# resulting misclassifications.csv). Four of six have been addressed; two
# remain flagged for deliberate review.
#
# Addressed (2026-09-09, following the S151 Heat/Magic narrowing pattern):
#
#   "Bermuda"     (was in `countries`)      — REMOVED. Collided with "Bermuda
#                                             Shorts" as a garment cut
#                                             (~13 FPs, all Lulu); no heraldic
#                                             concept is Bermuda-branded in
#                                             this population.
#   "Roma"        (was in `soccer_teams`)   — narrowed to "AS Roma". The
#                                             bare form collided with Puma's
#                                             "Roma" sneaker line (5 FPs).
#   "RM"          (was in `soccer_teams`)   — REMOVED. Two-letter abbreviation
#                                             collided with UA's "RM" model
#                                             suffix (3 FPs); "Real Madrid"
#                                             on the same line still catches
#                                             the club.
#   "Athletics"   (was in `baseball_teams`) — narrowed to "Oakland Athletics".
#                                             Generic English word collided
#                                             with athletic-apparel usage
#                                             (2 FPs).
#
# Still open (deliberately deferred):
#
#   "collegiate"  (in `keywords` below)  — Lulu & UA use it as a style-line
#                                          name (~16 FPs); does not catch
#                                          any heraldic product in the FN
#                                          set that other entries miss.
#   "Las Vegas"   (in `cities`)          — Puma's Las Vegas-themed graphics
#                                          line (~10 FPs); replace with
#                                          "Las Vegas Raiders" / "Aces" only.
#
# Long tail (1-2 FPs each, lower priority): "Boston", "Arizona", "Canada",
# "Singapore", "Austin", "Phoenix", "Japan", "Boca Juniors", "Juventus".
# ============================================================================


model_excluded_products = [

    ]

keywords = [
        "college", "soccer ball", "FC", "baseball pants", "collegiate"
    ]
    
states = [
        "Alabama", "Alaska", "Arizona", "Arkansas", "California", "Colorado",
        "Connecticut", "Delaware", "Florida", "Georgia", "Hawaii", "Idaho",
        "Illinois", "Indiana", "Iowa", "Kansas", "Kentucky", "Louisiana",
        "Maine", "Maryland", "Massachusetts", "Michigan", "Minnesota",
        "Mississippi", "Missouri", "Montana", "Nebraska", "Nevada",
        "New Hampshire", "New Jersey", "New Mexico", "New York",
        "North Carolina", "North Dakota", "Ohio", "Oklahoma", "Oregon",
        "Pennsylvania", "Rhode Island", "South Carolina", "South Dakota",
        "Tennessee", "Texas", "Utah", "Vermont", "Virginia", "Washington",
        "West Virginia", "Wisconsin", "Wyoming"
    ]

cities = [
    "New York", "Los Angeles", "Chicago", "Houston", "Phoenix",
    "Philadelphia", "San Antonio", "San Diego", "Dallas", "San Jose",
    "Austin", "Jacksonville", "Fort Worth", "Columbus", "San Francisco",
    "Charlotte", "Indianapolis", "Seattle", "Denver", "Washington",
    "Boston", "El Paso", "Nashville", "Detroit", "Oklahoma City",
    "Portland", "Las Vegas", "Memphis", "Louisville", "Baltimore"
]

colleges = [
    # SEC
    "Alabama", "Auburn", "Florida", "Georgia", "Kentucky", "LSU", "Ole Miss", "Mississippi State", "Tennessee",
    
    # ACC
    "Clemson", "Duke", "Florida State", "North Carolina", "NC State", "Virginia", "Virginia Tech", "Miami", "Syracuse", "Boston College",
    
    # Big Ten
    "Ohio State", "Michigan", "Penn State", "Wisconsin", "Iowa", "Nebraska", "Michigan State", "Minnesota", "Purdue", "Indiana",
    
    # Big 12
    "Oklahoma", "Texas", "Baylor", "Kansas", "Kansas State", "TCU", "West Virginia",
    
    # Pac-12
    "USC", "UCLA", "Oregon", "Washington", "Stanford", "Arizona", "Arizona State", "Colorado"
]

countries = [
        "Afghanistan", "Albania", "Algeria", "American Samoa", "Andorra",
        "Angola", "Antigua and Barbuda", "Argentina", "ARG", "Armenia", "Aruba",
        "Australia", "AUS", "Austria", "Azerbaijan", "Bahamas", "Bahrain",
        "Bangladesh", "Barbados", "Belarus", "Belgium", "Belize", "Benin",
        "Bhutan", "Bolivia", "Bosnia and Herzegovina", "BiH",
        "Botswana", "Brazil", "British Virgin Islands", "Brunei",
        "Bulgaria", "Burkina Faso", "Burundi", "Cambodia", "Cameroon", "Canada",
        "Cape Verde", "Cayman Islands", "Central African Republic", "Chad",
        "Chile", "China", "Chinese Taipei", "Colombia", "Comoros", "Congo",
        "Dominican Republic of the Congo", "DR Congo", "Cook Islands",
        "Costa Rica", "Côte d'Ivoire", "Croatia", "Cuba", "Curaçao", "Cyprus",
        "Czech Republic", "CZE", "Denmark", "Djibouti", "Dominica",
        "Dominican Republic", "DOM", "Ecuador", "Egypt", "El Salvador",
        "England", "Equatorial Guinea", "Eritrea", "Estonia", "Eswatini",
        "Ethiopia", "Faroe Islands", "Fiji", "Finland", "France", "Gabon",
        "Gambia", "Georgia", "Germany", "GER", "Ghana", "Gibraltar", "Greece",
        "Grenada", "Guadeloupe", "Guam", "Guatemala", "Guinea", "Guinea-Bissau",
        "Guyana", "Haiti", "Honduras", "Hong Kong", "Hungary", "Iceland", "India",
        "Indonesia", "Iran", "IRI", "Iraq", "Ireland", "Israel", "Italy", "ITA",
        "Jamaica", "Japan", "Kazakhstan", "Kenya", "Kiribati", "Kosovo",
        "Kuwait", "Kyrgyzstan", "Laos", "Latvia", "Lebanon", "Lesotho", "Liberia",
        "Libya", "Liechtenstein", "Lithuania", "Luxembourg", "Macau",
        "Madagascar", "Malawi", "Malaysia", "Maldives", "Mali", "Malta",
        "Marshall Islands", "Martinique", "Mauritania", "Mauritius", "Mexico",
        "Micronesia", "FSM", "Moldova", "Monaco", "Mongolia", "Montenegro",
        "Montserrat", "Morocco", "Mozambique", "Myanmar", "Namibia", "Nauru",
        "Nepal", "Netherlands", "New Caledonia", "New Zealand", "Nicaragua",
        "Niger", "Nigeria", "Northern Ireland", "Norway", "Oman", "Pakistan",
        "Palau", "Palestine", "Panama", "Papua New Guinea", "Paraguay", "Peru",
        "Philippines", "Pitcairn Islands", "Poland", "Portugal", "Puerto Rico",
        "Qatar", "Romania", "Russian Federation", "RUS", "Rwanda",
        "Saint Kitts and Nevis", "Saint Lucia", "Saint Vincent and the Grenadines",
        "Samoa", "San Marino", "São Tomé and Príncipe", "Saudi Arabia", "Scotland",
        "Senegal", "Serbia", "Seychelles", "Sierra Leone", "Singapore", "Slovakia",
        "Slovenia", "Solomon Islands", "Somalia", "South Africa", "South Korea",
        "KOR", "South Sudan", "Spain", "Sri Lanka", "Sudan", "Suriname", "Sweden",
        "Switzerland", "Syria", "Tajikistan", "Tanzania", "Thailand", "Timor-Leste",
        "Togo", "Tonga", "Trinidad and Tobago", "Tunisia", "Turkey",
        "Turks and Caicos Islands", "Turkmenistan", "Tuvalu", "Uganda", "Ukraine",
        "United Arab Emirates", "UAE", "United States of America", "USA", "U.S.", "Uruguay",
        "Uzbekistan", "Vanuatu", "Vatican City", "Venezuela", "Vietnam", "Wales",
        "Yemen", "Zambia", "Zimbabwe"
    ]
    
sports = [
        "NFL", "NBA", "CFL", "XFL", "USFL", "ELF", "MLB", "NPB", "KBO", "CPBL", "LMB",
        "WNBA", "EuroLeague", "CBA", "NBL", "PBA", "NHL", "KHL", "SHL",
        "Liiga", "DEL", "NL", "EPL", "EFL", "SPFL", "LaLiga", "Serie A", "Bundesliga",
        "Ligue 1", "Primeira Liga", "Eredivisie", "RPL", "MLS", "Liga MX", "Primera División",
        "Brasileirão", "J1 League", "K League 1", "CSL", "A-League", "ISL", "PSL",
        "NRL", "Super Rugby", "Premiership Rugby", "URC", "MLR", "IPL", "BBL", "PSL",
        "CPL", "BPL", "AFL", "SANFL", "VFL", "OWL", "LCS", "CDL", "SuperLega", "Superliga", "NWSL"
    ]
    
soccer_teams = [
    # European clubs
    "Arsenal", "Aston Villa", "Bournemouth", "Brentford", "Brighton & Hove Albion",
    "Chelsea", "Crystal Palace", "Everton", "Fulham", "Liverpool", "Luton Town",
    "Manchester City", "MCFC", "Manchester United", "MUFC", "Newcastle United",
    "Nottingham Forest", "Tottenham Hotspur", "Spurs", "West Ham United",
    "Wolverhampton Wanderers", "Wolves", "Real Madrid", "Barcelona",
    "Barça", "Atlético Madrid", "Atleti", "Sevilla", "Valencia", "Villarreal",
    "Real Sociedad", "Athletic Bilbao", "Real Betis", "RCD Espanyol", "Juventus",
    "Milan", "Inter Milan", "AS Roma", "Napoli", "Lazio", "Fiorentina", "Atalanta",
    "Torino", "Sampdoria", "FC Bayern Munich", "FCB", "Bayern", "Borussia Dortmund",
    "BVB", "RB Leipzig", "Bayer 04 Leverkusen", "Eintracht Frankfurt",
    "Borussia Mönchengladbach", "VfL Wolfsburg", "Union Berlin", "Paris Saint-Germain",
    "PSG", "Olympique de Marseille", "OM", "Olympique Lyonnais", "AS Monaco",
    "Lille OSC", "RC Lens", "AFC Ajax", "PSV Eindhoven", "Feyenoord Rotterdam",
    "AZ Alkmaar", "Porto", "SL Benfica", "Sporting CP", "Club Brugge KV",
    "RSC Anderlecht", "Standard Liège", "KAA Gent", "Galatasaray SK",
    "Fenerbahçe SK", "Beşiktaş JK", "Trabzonspor",

    # North American clubs (including additional NWSL teams)
    "Angel City", "Portland Thorns FC", "OL Reign", "Chicago Red Stars", "North Carolina Courage",
    "Racing Louisville FC", "LA Galaxy", "Seattle Sounders FC", "Atlanta United FC",
    "New York City FC", "Inter Miami CF", "D.C. United", "USMNT", "USWNT",

    # South American clubs
    "Boca Juniors", "River Plate", "Flamengo", "Palmeiras", "Corinthians", "Santos",
    "São Paulo", "Fluminense", "Internacional", "Grêmio",

    # African clubs
    "Al Ahly", "Zamalek", "TP Mazembe", "Mamelodi Sundowns", "Wydad Casablanca", "Raja Casablanca",

    # Asian clubs
    "Guangzhou Evergrande", "Beijing Guoan", "Shanghai Port", "Kashima Antlers",
    "Urawa Red Diamonds", "Jeonbuk Hyundai Motors", "Al Hilal", "Al Nassr",
    "Persepolis", "Esteghlal",

    # Oceania clubs
    "Auckland City FC", "Waitakere United",

    # National teams
    "England National Team", "Germany National Team", "Brazil National Team",
    "Argentina National Team", "France National Team", "Italy National Team",
    "Spain National Team"
]

football_teams = [
    "Cardinals", "Falcons", "Ravens", "Bills", "Panthers", "Bears", "Bengals", "Browns",
    "Cowboys", "Broncos", "Lions", "Packers", "Texans", "Colts", "Jaguars", "Chiefs",
    "Dolphins", "Vikings", "Patriots", "Saints", "Giants", "Jets", "Raiders", "Eagles",
    "Steelers", "49ers", "Seahawks", "Buccaneers", "Titans", "Commanders", "Chargers", "Rams"
]

baseball_teams = [
    # American League East
    "Yankees", "Red Sox", "Blue Jays", "Orioles", "Rays",
    
    # American League Central
    "Guardians", "Tigers", "Royals", "Twins", "White Sox",
    
    # American League West
    "Astros", "Angels", "Oakland Athletics", "Mariners", "Rangers",
    
    # National League East
    "Mets", "Braves", "Phillies", "Marlins", "Nationals",
    
    # National League Central
    "Cubs", "Cardinals", "Brewers", "Reds", "Pirates",
    
    # National League West
    "Dodgers", "Giants", "Padres", "Rockies", "Diamondbacks"
]

basketball_teams = [
    "Hawks", "Celtics", "Nets", "Hornets", "Bulls", "Cavaliers", "Mavericks",
    "Nuggets", "Pistons", "Warriors", "Rockets", "Pacers", "Clippers", "Lakers",
    "Grizzlies", "Miami Heat", "Bucks", "Timberwolves", "Pelicans", "Knicks", "Thunder",
    "Orlando Magic", "76ers", "Suns", "Trail Blazers", "Kings", "Spurs", "Raptors", "Jazz", "Wizards"
]

hockey_teams = [
    "Flames"
]



# Combine all segments into one list.
HERALDIC_KEYWORDS = model_excluded_products + keywords + states + cities + colleges + countries + sports + soccer_teams + football_teams + baseball_teams + basketball_teams + hockey_teams