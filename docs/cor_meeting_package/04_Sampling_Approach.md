# Sampling Approach

**As submitted in D2 §5.1, 9 July 2026**

| Parameter | Value | Basis |
| --- | --- | --- |
| Total population | 94,231 unique connections | Per Q1 of the solicitation Q&A; to be confirmed with the COR on data receipt |
| Confidence level | **95%** | Contract floor — §C Tasks 3 and 4 |
| Margin of error | **±5%** | Standard audit precision (AGT methodology) |
| Required sample size | **383 entities** | Cochran with finite population correction |
| Method | **Stratified random sampling across all 11 QHINs** | §C requires a sample "from each QHIN" |
| Allocation | Proportional to QHIN population | Ensures each QHIN is represented |

## What is fixed by the contract and what is AGT's

**Contract-fixed:** the ≥95% confidence level; a representative sample from each
QHIN; that the sample size is determined by the confidence level; and that the
sampling methodology and confidence interval calculations are submitted under
Task 2 — which they were.

**AGT methodology, disclosed:** the ±5% margin of error, the stratification
variable (managing QHIN), proportional allocation, and the treatment of QHINs too
small to sample.

## Reproducibility

For the drawn sample AGT records the frame, the population snapshot and its hash,
the random seed, the draw timestamp, the per-QHIN allocation, and the selected
entity identifiers — so the same sample can be retrieved and re-examined.

## What has not happened

**The official sample has not been drawn.** Stratum allocations cannot be
finalised until the COR-provided data is received. D2 commits AGT to finalising
them **within three business days of data receipt** and sharing them with the COR
**before any review begins**.

## Population screening is separate

The DocuAction platform can screen an entire delivered population automatically.
Where that is done it is operational intelligence supporting stratification and
quality control. **It is not a substitute for the contractual sample**, and no
report will present a screened population as a sampled result.
