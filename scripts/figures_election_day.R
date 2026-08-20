# Election-day website behaviour across Florida counties.
#
# Three figures from manifest/fl-county-attributes.csv:
#   1. fig1-behavior-map.png   county choropleth, what each county did
#   2. fig2-vendor-map.png     same map, filled by website vendor
#   3. fig3-vr-population.png  population of VR Systems counties, split by whether
#                              they switched to an election-night page
#
# Figures 1 and 2 are meant to be read together: the behaviour clusters in north
# Florida, and the vendor map shows why that is only part of the story — 48 counties
# run the same vendor and only 10 switched.
#
# Run:  Rscript scripts/figures_election_day.R

library(ggplot2)
library(dplyr)
library(readr)
library(scales)
library(patchwork)
library(sf)
library(ggmedsl)

medsl_fonts(dpi = 300)   # must precede any plotting or the brand fonts fall back

# Resolve the repo root from the script's own path when run via Rscript, falling
# back to the working directory. `sys.frame()` is not usable here — Rscript has no
# such frame on the stack.
args_all <- commandArgs(trailingOnly = FALSE)
file_arg <- sub("^--file=", "", grep("^--file=", args_all, value = TRUE))
root <- if (length(file_arg) == 1) dirname(dirname(normalizePath(file_arg))) else "."
if (!dir.exists(file.path(root, "manifest"))) root <- "."
attr_path <- file.path(root, "manifest", "fl-county-attributes.csv")
out_dir   <- file.path(root, "manifest", "figures")
dir.create(out_dir, showWarnings = FALSE, recursive = TRUE)

src <- paste("MEDSL fl-county-watch snapshots (2026-08-06 to 2026-08-18);",
             "USDA ERS rural-urban continuum codes")

cty <- read_csv(attr_path, show_col_types = FALSE) %>%
  mutate(
    fips = sprintf("%05d", as.integer(fips)),
    # The CSV carries lowercase "true"/"false", which readr auto-parses to logical.
    # Comparing that to the string "true" silently yields FALSE for every row and
    # collapses the whole comparison — normalize to a real logical instead.
    went_election_night = as.character(went_election_night) %in%
      c("true", "TRUE", "True")
  )

# --- 1. What each county did ------------------------------------------------
# Unordered, non-partisan categories, so the categorical palette — a blue/red read
# here would imply a partisan split the data does not contain.
behaviour_levels <- c(
  "Election-night page, old links 404",
  "Election-night page, old links serve it",
  "Edited",
  "Not comparable",
  "No change"
)
map1_df <- cty %>%
  transmute(fips, behaviour = factor(status, levels = behaviour_levels))

# Geometry from tigris/sf rather than ggmedsl's medsl_map_categorical(), which
# requires usmap. tigris + sf are the installed stack, and composing the map by
# hand still uses the brand scale and the map theme, so the output is identical in
# palette and furniture.
fl <- tigris::counties(state = "FL", cb = TRUE, year = 2022,
                       progress_bar = FALSE) %>%
  sf::st_transform(4326) %>%
  select(fips = GEOID, geometry)

p1 <- ggplot(left_join(fl, map1_df, by = "fips")) +
  geom_sf(aes(fill = behaviour), color = "white", linewidth = 0.15) +
  scale_fill_medsl_categorical(name = "Election-day behavior",  # sentence case
                               na.value = "#C4C4C4") +
  labs(
    # Descriptive, not a conclusion. Title Case, "by" stays lowercase.
    title    = "Election-Day Website Behavior by County",
    subtitle = "67 Florida counties, 2026 primary election",
    caption  = medsl_caption(source = src)
  ) +
  theme_medsl_map() +
  # Five labels this long overflow a single legend row and get clipped at both
  # panel edges, so wrap to two rows.
  guides(fill = guide_legend(nrow = 2, byrow = TRUE)) +
  theme(legend.position = "bottom", legend.text = element_text(size = 8))

ggsave_medsl(file.path(out_dir, "fig1-behavior-map.png"), p1,
             width = 7.5, height = 7, dpi = 300)

# --- 2. Vendor --------------------------------------------------------------
map2_df <- cty %>%
  transmute(fips, vendor = factor(vendor,
                                  levels = c("VR Systems", "CivicPlus",
                                             "WordPress", "other")))

p2 <- ggplot(left_join(fl, map2_df, by = "fips")) +
  geom_sf(aes(fill = vendor), color = "white", linewidth = 0.15) +
  scale_fill_medsl_categorical(name = "Website vendor",
                               na.value = "#C4C4C4") +
  labs(
    title    = "Supervisor of Elections Website Vendor by County",
    subtitle = "67 Florida counties, identified from captured page markup",
    caption  = medsl_caption(source = src)
  ) +
  theme_medsl_map() +
  guides(fill = guide_legend(nrow = 1)) +
  theme(legend.position = "bottom", legend.text = element_text(size = 8))

ggsave_medsl(file.path(out_dir, "fig2-vendor-map.png"), p2,
             width = 7.5, height = 7, dpi = 300)

# --- 2b. The two maps side by side -----------------------------------------
# Read together they carry the actual point, so ship the pair as one figure too.
pair <- (p1 + labs(title = NULL, subtitle = NULL, caption = NULL) |
         p2 + labs(title = NULL, subtitle = NULL, caption = NULL)) +
  plot_annotation(
    title    = "Election-Day Website Behavior and Vendor by County",
    subtitle = "67 Florida counties, 2026 primary election",
    caption  = medsl_caption(source = src),
    theme    = theme_medsl_map()
  )

ggsave_medsl(file.path(out_dir, "fig2b-behavior-and-vendor.png"), pair,
             width = 13, height = 6.5, dpi = 300)

# --- 3. Population within VR Systems counties ------------------------------
# The comparison group that matters. Across all 67 counties, vendor confounds size:
# small counties buy the shared platform. Holding vendor fixed isolates the size
# question — and 10 of these 48 switched.
vr <- cty %>%
  filter(vendor == "VR Systems", !is.na(population_2020)) %>%
  mutate(
    switched = factor(
      ifelse(went_election_night,
             "Switched to election-night page", "Kept its normal site"),
      levels = c("Switched to election-night page", "Kept its normal site")
    )
  )

switch_colors <- c(
  "Switched to election-night page" = unname(medsl_colors[["red"]]),
  "Kept its normal site"            = unname(medsl_colors[["steel"]])
)

set.seed(1)   # jitter is cosmetic, but a fixed seed keeps the figure reproducible
p3 <- ggplot(vr, aes(x = population_2020, y = switched, color = switched)) +
  geom_jitter(height = 0.18, width = 0, size = 2.6, alpha = 0.85,
              show.legend = FALSE) +
  scale_x_log10(labels = label_comma(),
                breaks = c(1e4, 3e4, 1e5, 3e5, 1e6, 3e6),
                # Headroom so the 3,000,000 tick label isn't clipped by the panel.
                expand = expansion(mult = c(0.04, 0.09))) +
  scale_color_manual(values = switch_colors) +
  labs(
    title    = "Population of VR Systems Counties by Election-Day Behavior",
    subtitle = "48 Florida counties running the same website vendor",
    x        = "Population, 2020 census (log scale)",   # digit token is case-exempt
    y        = NULL,
    caption  = medsl_caption(source = src),
    tag      = paste("Vendor held fixed because it confounds size:",
                     "smaller counties disproportionately run the shared platform.")
  ) +
  theme_medsl() +
  theme(plot.tag.position = c(0.99, 0.02),
        plot.tag = element_text(size = 7, colour = "#666666",
                                hjust = 1, vjust = 0))

ggsave_medsl(file.path(out_dir, "fig3-vr-population.png"), p3,
             width = 9, height = 4.2, dpi = 300)

# --- console summary -------------------------------------------------------
cat("\nwithin VR Systems counties:\n")
vr %>%
  group_by(switched) %>%
  summarise(n = n(),
            min_pop = min(population_2020),
            median_pop = median(population_2020),
            max_pop = max(population_2020),
            .groups = "drop") %>%
  as.data.frame() %>%
  print(row.names = FALSE)

cat("\nby rurality (all 67):\n")
cty %>%
  group_by(rucc_label) %>%
  summarise(counties = n(),
            switched = sum(went_election_night),
            .groups = "drop") %>%
  arrange(rucc_label) %>%
  as.data.frame() %>%
  print(row.names = FALSE)

cat("\nwrote figures to ", out_dir, "\n", sep = "")
