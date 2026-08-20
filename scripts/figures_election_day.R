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

src <- paste("MEDSL fl-county-watch snapshots (2026-08-06 to 2026-08-20);",
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
    title    = "Website Behavior in the Two Days After the Election",
    subtitle = "67 Florida counties, 2026-08-18 to 2026-08-20",
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
plat_levels <- c("WordPress", "CivicPlus", "DotNetNuke", "Drupal",
                 "other/unknown")
map2_df <- cty %>%
  transmute(fips, platform = factor(platform, levels = plat_levels))

# Both maps are categorical, so both would otherwise draw the first hues of the
# categorical palette — and side by side that makes green mean "Edited" on the left
# and "WordPress" on the right, which misreads at a glance. Give vendor its own
# hand-built scale from medsl_colors (the pattern standard_bar.R uses) picking
# brand colors the behaviour scale does not touch.
platform_colors <- c(
  "WordPress"     = unname(medsl_colors[["steel"]]),
  "CivicPlus"     = unname(medsl_colors[["olive"]]),
  "DotNetNuke"    = unname(medsl_colors[["crimson"]]),
  "Drupal"        = unname(medsl_colors[["lime"]]),
  "other/unknown" = unname(medsl_colors[["navy"]])
)

p2 <- ggplot(left_join(fl, map2_df, by = "fips")) +
  geom_sf(aes(fill = platform), color = "white", linewidth = 0.15) +
  scale_fill_manual(name = "Website platform", values = platform_colors,
                    na.value = "#C4C4C4", drop = FALSE) +
  labs(
    title    = "Supervisor of Elections Website Platform by County",
    subtitle = "67 Florida counties, identified from same-host page markup",
    tag      = paste("Platform is the CMS that builds the site. It is deliberately",
                     "separate from the election-services vendor a county links out",
                     "to \u2014 60 of 67 link VR Systems regardless of platform."),
    caption  = medsl_caption(source = src)
  ) +
  theme_medsl_map() +
  guides(fill = guide_legend(nrow = 1)) +
  theme(legend.position = "bottom", legend.text = element_text(size = 8),
        plot.tag.position = c(0.99, 0.015),
        plot.tag = element_text(size = 7, colour = "#666666",
                                hjust = 1, vjust = 0))

ggsave_medsl(file.path(out_dir, "fig2-platform-map.png"), p2,
             width = 7.5, height = 7, dpi = 300)

# --- 2b. The two maps side by side -----------------------------------------
# Read together they carry the actual point, so ship the pair as one figure too.
# Each panel keeps its own legend (the two fills are different scales, so
# patchwork cannot merge them). Side by side each legend gets half the width, and
# a legend whose title sits to its LEFT with long labels on two rows overruns that
# — which is what clipped the bottom-left block. Moving each title above its keys
# and adding a row reclaims the width.
legend_fix <- function(p, rows) {
  p +
    guides(fill = guide_legend(title.position = "top", nrow = rows,
                               byrow = TRUE)) +
    theme(legend.position   = "bottom",
          legend.title      = element_text(size = 9),
          legend.text       = element_text(size = 8),
          legend.key.size   = unit(0.9, "lines"),
          legend.box.margin = margin(t = 4))
}

pair <- (legend_fix(p1 + labs(title = NULL, subtitle = NULL, caption = NULL), 3) |
         legend_fix(p2 + labs(title = NULL, subtitle = NULL, caption = NULL), 2)) +
  plot_annotation(
    title    = "Post-Election Website Behavior and Platform by County",
    subtitle = "67 Florida counties, 2026-08-18 to 2026-08-20",
    caption  = medsl_caption(source = src),
    # No note here: plot_annotation() has no tag slot (its `tag_levels` labels
    # panels, not the figure), and the panels now use disjoint palettes anyway, so
    # there is nothing left to caveat.
    theme    = theme_medsl_map()
  )

ggsave_medsl(file.path(out_dir, "fig2b-behavior-and-platform.png"), pair,
             width = 13, height = 7.6, dpi = 300)

# --- 3. Population within VR Systems counties ------------------------------
# The comparison group that matters. Across all 67 counties, vendor confounds size:
# small counties buy the shared platform. Holding vendor fixed isolates the size
# question — and 10 of these 48 switched.
vr <- cty %>%
  filter(platform == "WordPress", !is.na(population_2020)) %>%
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
    title    = "Population of WordPress Counties by Election-Day Behavior",
    subtitle = "21 Florida counties on the same website platform",
    x        = "Population, 2020 census (log scale)",   # digit token is case-exempt
    y        = NULL,
    caption  = medsl_caption(source = src),
    tag      = paste("Platform held fixed: all 10 counties that switched run",
                     "WordPress, so comparing them to the other 11 WordPress",
                     "counties isolates size from platform.")
  ) +
  theme_medsl() +
  theme(plot.tag.position = c(0.99, 0.02),
        plot.tag = element_text(size = 7, colour = "#666666",
                                hjust = 1, vjust = 0))

ggsave_medsl(file.path(out_dir, "fig3-wordpress-population.png"), p3,
             width = 9, height = 4.2, dpi = 300)

# --- 4. Vendor by rurality -------------------------------------------------
# Florida uses only RUCC 1,2,3,4,6,8,9 — codes 5 and 7 have no counties — and
# several of those hold a single county. Collapsing to five ordered bands keeps
# every group large enough to read while preserving the urban-to-rural order.
rucc_band <- function(x) {
  dplyr::case_when(
    x == 1 ~ "Large metro (1M+)",
    x == 2 ~ "Medium metro (250k-1M)",
    x == 3 ~ "Small metro (<250k)",
    x %in% c(4, 5, 6, 7) ~ "Nonmetro, has an urban core",
    x %in% c(8, 9) ~ "Nonmetro, rural",
    TRUE ~ NA_character_
  )
}
band_levels <- c("Large metro (1M+)", "Medium metro (250k-1M)",
                 "Small metro (<250k)", "Nonmetro, has an urban core",
                 "Nonmetro, rural")

band_df <- cty %>%
  mutate(band = factor(rucc_band(rucc), levels = band_levels),
         platform = factor(platform, levels = plat_levels)) %>%
  count(band, platform, .drop = FALSE) %>%
  group_by(band) %>%
  mutate(band_n = sum(n)) %>%
  ungroup() %>%
  mutate(band_label = sprintf("%s  (n = %d)", band, band_n))

label_levels <- band_df %>%
  distinct(band, band_label) %>%
  arrange(band) %>%
  pull(band_label)
band_df$band_label <- factor(band_df$band_label, levels = rev(label_levels))

# Counts, not shares: one band holds a single county, and a 100% stacked bar would
# render that as a full-width block indistinguishable from a unanimous group of 22.
p4 <- ggplot(band_df, aes(x = n, y = band_label, fill = platform)) +
  # reverse = TRUE so segments stack in legend order, putting the dominant vendor
  # against the axis: VR Systems' count is then directly comparable across bands
  # instead of starting at a different offset in every bar.
  geom_col(width = 0.68, position = position_stack(reverse = TRUE)) +
  # Same vendor -> color mapping as the map, so a vendor keeps its color across
  # every figure here.
  scale_fill_manual(name = "Website platform", values = platform_colors,
                    drop = FALSE) +
  scale_x_continuous(expand = expansion(mult = c(0, 0.04))) +
  labs(
    title    = "Website Platform by County Rurality",
    subtitle = "67 Florida counties, grouped by USDA rural-urban continuum code",
    x        = "Counties",
    y        = NULL,
    caption  = medsl_caption(source = src),
    tag      = paste("Continuum codes 5 and 7 have no Florida counties;",
                     "codes 4-7 and 8-9 are collapsed to keep group sizes readable.")
  ) +
  theme_medsl() +
  theme(plot.tag.position = c(0.99, 0.02),
        plot.tag = element_text(size = 7, colour = "#666666",
                                hjust = 1, vjust = 0))

ggsave_medsl(file.path(out_dir, "fig4-platform-by-rurality.png"), p4,
             width = 9.5, height = 4.6, dpi = 300)

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

cat("\nplatform by rurality band:\n")
band_df %>%
  filter(n > 0) %>%
  select(band, platform, n) %>%
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
