# Evaluation protocol proposal

**Status:** proposed for team review; not the missing original E1-E8 contract.

The repository names four baselines and eight evaluation metrics, but it does not
define the metric names or operational tests for E1-E8, or provide B1-B3 runners.
This proposal uses measurements already available in the implementation and
Mininet testbed. Accepting it would define a new, explicit operational contract;
it must not be cited as the original project plan.

## Baseline conditions

| Baseline | Proposed condition | Required controls |
|---|---|---|
| B0 | Static shortest-path forwarding; no intents and no monitoring. | Use the existing B0 driver and fixed h1-to-h2 traffic. |
| B1 | Install one feasible intent and its route, then leave it open loop. | Do not start telemetry or subscribe the controller to violation events. |
| B2 | Monitor one intent and react to a confirmed violation using relaxed/direct updates, with dwell disabled. | Use the same intent and traffic as B3. A separate conflict request must be included to measure the effect of omitting admission control. |
| B3 | Full implementation: conflict-aware admission, strict two-phase updates and detector hysteresis/dwell. | Use the same topology, intent, offered load and failure/violation trace as B1/B2. |

The repository currently supports B0 and B3's single-intent live closed-loop
scenario. B1 needs an open-loop runner; B2 needs explicit control of update mode,
detector damping and admission. The single-intent ST-7 scenario cannot establish
the value of conflict checking by itself.

## Candidate metrics

These are proposed IDs and definitions, not source-plan definitions. Report a
metric as not applicable when its baseline has no corresponding mechanism.

| ID | Proposed metric | Operational definition | Evidence |
|---|---|---|---|
| E1 | Intent activation latency (ms) | Time from sending an accepted intent request to the first persisted active state with an installed path. | API client timestamps plus SQLite intent/path rows. |
| E2 | Violation detection latency (ms) | Time from the first sample in a qualifying consecutive-breach sequence to the persisted confirmed `sla.violated` event. | Measurement timestamps and event timestamp; includes detector smoothing/confirmation. |
| E3 | Recovery latency (ms) | Time from confirmed `sla.violated` to the causal `path.installed` event for the replacement path. | Persisted event timestamps and path revision. |
| E4 | Update interruption (lost probes, %) | Lost sequence-numbered UDP probes divided by probes sent in a fixed window around a route update. Compare relaxed and strict updates under the same offered load. | Host probe log and update start/finish timestamps. |
| E5 | Peak flow-table occupancy (rules/switch) | Maximum active flow count on any switch while the new strict version is staged and the old version remains installed. | `ovs-ofctl dump-flows` snapshots during the ST-10 transaction. |
| E6 | Reroute count (count/run) | Number of successful route changes during a fixed alternating breach/recovery trace. Compare dwell disabled and enabled. | `db.reroute_count`, path history and suppression audit events. |
| E7 | SLA compliance (%) | Complete telemetry samples satisfying all declared guarantees divided by complete samples for those guarantees. Exclude missing metric values and report their count separately. | Persisted M5 measurements and the submitted intent guarantee. |
| E8 | Control-plane work (messages/run) | Count FlowMods, barriers and PacketIns observed during a fixed traffic and update window; also report Ryu CPU seconds and peak RSS separately. | Ryu/OpenFlow instrumentation and process resource counters; instrumentation is not yet implemented. |

E5's preliminary pilot found that OVS 2.17.12 retained 2,000 unique rules under
configured `other-config:flow-limit` values from 100 to 5,000. This shows no
enforced cap in that software-switch setup; it does not model hardware TCAM.

## Repetition and reporting

Use ten independent repetitions per baseline, with the same topology, intent,
traffic duration, offered load, event trace and controller settings. Record raw
measurements before aggregation. Summarize each run first, then report the mean,
sample standard deviation, sample count and two-sided 95% Student t interval. Keep
the raw CSV and a script that regenerates every included figure. Do not compare
latency or loss values across baselines when the offered traffic or route window
differs.

No RNG is currently used in the B0 driver. If future scenarios randomize event
timing or traffic, record a fixed seed for every run. Do not infer E1-E8 labels for
existing B0 columns until the team accepts the mapping.

## Work needed after review

1. Confirm or replace these definitions with the original metric contract.
2. Implement B1 and B2 harness controls, including a real contention case for the
   admission-control comparison.
3. Add E1-E8 instrumentation without changing the frozen intent grammar.
4. Run ten repetitions per approved baseline, summarize confidence intervals and
   regenerate figures through `experiments/run_all.sh`.
5. Review the resulting comparisons before drafting the final ST-14 conclusions.
