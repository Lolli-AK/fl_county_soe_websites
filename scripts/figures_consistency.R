# Do county election sites state the operational facts a voter needs, correctly?
#
# One tile per (county, fact) from manifest/fl-consistency.csv. Counties are sorted
# by how many of the four facts they state, so the dominant pattern — coverage, not
# contradiction — is visible without reading a single label.
#
# Run:  Rscript scripts/figures_consistency.R

library(ggplot2)
library(dplyr)
library(readr)
library(ggmedsl)

medsl_fonts(dpi = 300)

args_all <- commandArgs(trailingOnly = FALSE)
file_arg <- sub("^--file=", "", grep("^--file=", args_all, value = TRUE))
root <- if (length(file_arg) == 1) dirname(dirname(normalizePath(file_arg))) else "."
if (!dir.exists(file.path(root, "manifest"))) root <- "."
out_dir <- file.path(root, "manifest", "figures")
dir.create(out_dir, showWarnings = FALSE, recursive = TRUE)

src <- "MEDSL fl-county-watch snapshots, 2026-08-20"

fact_labels <- c(
  poll_hours            = "Polling hours",
  election_date         = "Next election date",
  registration_deadline = "Registration deadline",
  early_voting          = "Early voting window"
)

cons <- read_csv(file.path(root, "manifest", "fl-consistency.csv"),
                 show_col_types = FALSE) %>%
  mutate(
    fact = factor(fact_labels[fact], levels = unname(fact_labels)),
    verdict = factor(
      recode(verdict,
             "matches"                = "States it, matches expected",
             "conflicts"              = "States something else (inspect)",
             "next election not named" = "Does not name the next election",
             "not stated"             = "Never states it"),
      levels = c("States it, matches expected",
                 "States something else (inspect)",
                 "Does not name the next election",
                 "Never states it"))
  )

# Sort counties by how much they state, so the coverage gradient is the visual.
order_df <- cons %>%
  group_by(county) %>%
  summarise(stated = sum(verdict != "Never states it"), .groups = "drop") %>%
  arrange(stated, county)
cons$county <- factor(cons$county, levels = order_df$county)

verdict_colors <- c(
  "States it, matches expected"     = unname(medsl_colors[["green"]]),
  "States something else (inspect)" = unname(medsl_colors[["gold"]]),
  "Does not name the next election" = unname(medsl_colors[["crimson"]]),
  "Never states it"                 = "#E4E4E4"
)

p <- ggplot(cons, aes(x = fact, y = county, fill = verdict)) +
  geom_tile(color = "white", linewidth = 0.6) +
  scale_fill_manual(name = NULL, values = verdict_colors) +
  scale_x_discrete(position = "top", expand = c(0, 0)) +
  scale_y_discrete(expand = c(0, 0)) +
  labs(
    # Descriptive: names what is plotted, asserts nothing about why.
    title    = "Operational Facts Stated on County Election Websites",
    subtitle = "67 Florida counties by four facts, checked against the statutory or statewide value",
    x = NULL, y = NULL,
    caption  = medsl_caption(source = src),
    tag      = paste("\"Never states it\" under-counts \u2014 phrasing varies,",
                     "so some counties state a fact in wording the extractor misses.")
  ) +
  theme_medsl() +
  theme(
    panel.grid       = element_blank(),
    axis.text.y      = element_text(size = 6.4),
    axis.text.x.top  = element_text(size = 9),
    legend.position  = "bottom",
    plot.tag.position = c(0.99, 0.028),
    plot.tag = element_text(size = 7, colour = "#666666", hjust = 1, vjust = 0)
  ) +
  guides(fill = guide_legend(nrow = 2, byrow = TRUE))

ggsave_medsl(file.path(out_dir, "fig5-fact-coverage.png"), p,
             width = 8.5, height = 11, dpi = 300)

# --- summary bar: how many of the four facts each county states ------------
counts <- order_df %>%
  count(stated) %>%
  mutate(stated = factor(stated, levels = 0:4,
                         labels = c("none", "1 of 4", "2 of 4", "3 of 4",
                                    "all 4")))

p2 <- ggplot(counts, aes(x = stated, y = n)) +
  geom_col(width = 0.66, fill = unname(medsl_colors[["steel"]])) +
  geom_text(aes(label = n), vjust = -0.5, size = 3.8,
            family = "StyreneB-Regular", color = "#444444") +
  scale_y_continuous(expand = expansion(mult = c(0, 0.14))) +
  labs(
    title    = "How Many of the Four Facts Each County States",
    subtitle = "67 Florida counties, 2026-08-20",
    x = "Facts stated", y = "Counties",
    caption  = medsl_caption(source = src)
  ) +
  theme_medsl()

ggsave_medsl(file.path(out_dir, "fig6-fact-count.png"), p2,
             width = 7.5, height = 4.2, dpi = 300)

cat("\ncoverage distribution:\n")
print(as.data.frame(counts), row.names = FALSE)
cat("\nwrote figures to ", out_dir, "\n", sep = "")
