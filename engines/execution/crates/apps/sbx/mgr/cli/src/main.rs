// Modified for Avenra. Original attribution and adaptation record: LICENSES/.
use clap::{Args, Parser};
use comfy_table::{Table, presets::UTF8_FULL_CONDENSED};
use avenra_runtime_common_cli::{exit_error, exit_message, unwrap_or_exit};
use avenra_runtime_common_core::ids::sandbox_label_or_id as sandbox_label;
use avenra_runtime_common_core::types::{
    AutoLifecycleConditionSet, AutoLifecycleConditionSetId, AutoLifecycleIdleBreakSource,
    AutoLifecyclePolicy, AutoLifecycleStageChain, AutoLifecycleWakePolicy, AutoLifecycleWakeSource,
    GuestUserName, PolicyAuthority, SandboxId, SandboxSelector, SimpleDuration,
};
use prost_types::Timestamp;
use std::collections::{BTreeMap, HashMap};

use avenra_runtime_net_common::proto::{
    EgressDefaultAction as NetworkPolicyAction, NetworkPolicyV1 as NetworkPolicyProto,
    NetworkPolicyVersion,
};
use avenra_runtime_node_api_client::proto::GetControllerStateRequest;
use avenra_runtime_sbx_mgr_client::proto::{
    BootMode, CleanupRequest, CreateRequest, CreateSnapshotRequest, CycleNetGatewayRequest,
    DeleteRequest, DeleteSnapshotsRequest, DisableSnapshotsRequest, EnableSnapshotsRequest,
    GetDiskUsageRequest, GetNetworkPolicyRequest, GetSandboxHealthRequest, KillRequest,
    ListSnapshotsRequest, ListTemplatesRequest, PauseRequest, ResumeRequest, RollbackRequest,
    SandboxInfo, SandboxListElement, SetAutoLifecyclePolicyRequest, SetNetworkPolicyRequest,
    SnapshotType, SpacedStartProcessRequest, StartRequest, StateRequest, StatsRequest, StopRequest,
    SuspendRequest, SuspendSnapshotFormat, UpdateRequest, WaitRequest, WakeRequest,
};

const FACET_NETWORK_POLICY: &str = "network-policies";
const FACET_SANDBOX_DESIRED_STATE: &str = "sandbox-desired-state";

mod args;
mod dispatch;
mod helpers;

use args::*;
use helpers::*;

#[tokio::main]
async fn main() {
    avenra_runtime_common_cli::init_logging("avenra-sbxmgr");
    let cli = Cli::parse();

    let mut client = avenra_runtime_sbx_mgr_client::connect()
        .await
        .unwrap_or_else(|e| {
            exit_error(format!(
                "cannot connect to sbx-manager: {e}\nIs avenra-sbx-manager running?"
            ));
        });

    dispatch::dispatch(cli.command, &mut client).await;
}
