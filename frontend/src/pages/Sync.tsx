import { useEffect, useState } from "react";
import {
  Box,
  Button,
  Card,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  FormControl,
  Grid,
  InputLabel,
  MenuItem,
  Paper,
  Select,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  Typography,
  Tooltip,
  IconButton,
  Snackbar,
  Alert,
  Chip,
} from "@mui/material";
import AddIcon from "@mui/icons-material/Add";
import PlayArrowIcon from "@mui/icons-material/PlayArrow";
import EditIcon from "@mui/icons-material/Edit";
import DeleteIcon from "@mui/icons-material/Delete";
import VisibilityIcon from "@mui/icons-material/Visibility";
import RefresherIcon from "@mui/icons-material/Refresh";

import {
  DataSource,
  SyncConfig,
  SyncConfigCreate,
  SyncFieldMapping,
  SyncLog,
  SyncResult,
  TargetColumnInfo,
} from "../types";
import { apiFetch } from "../lib/api";

interface ToastState {
  message: string;
  severity: "success" | "error" | "info" | "warning";
}

const emptyMapping = (): SyncFieldMapping => ({
  source_field: "",
  target_field: "",
  mapping_type: "direct",
  fixed_value: "",
  is_pk: false,
});

const createEmptyConfig = (): SyncConfigCreate => ({
  name: "",
  description: "",
  source_table: "",
  source_schema: "",
  source_db_alias: "default",
  target_datasource_id: 0,
  target_table: "",
  target_schema: "",
  sync_mode: "incremental",
  status: "active",
  timestamp_field: "updated_at",
  batch_size: 500,
  field_mappings: [emptyMapping()],
});

export default function SyncPage() {
  const [configs, setConfigs] = useState<SyncConfig[]>([]);
  const [datasources, setDatasources] = useState<DataSource[]>([]);
  const [logs, setLogs] = useState<SyncLog[]>([]);
  const [logsOpen, setLogsOpen] = useState(false);
  const [viewingLogsConfigId, setViewingLogsConfigId] = useState<number | null>(null);

  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingConfig, setEditingConfig] = useState<SyncConfigCreate | null>(null);
  const [editingId, setEditingId] = useState<number | null>(null);

  const [toast, setToast] = useState<ToastState | null>(null);

  // 加载数据
  useEffect(() => {
    loadConfigs();
    loadDatasources();
  }, []);

  const loadConfigs = async () => {
    try {
      const data = await apiFetch<SyncConfigList>("sync/configs");
      setConfigs(data.items);
    } catch {
      showToast("加载同步配置失败", "error");
    }
  };

  const loadDatasources = async () => {
    try {
      const data = await apiFetch<DataSourceList>("datasources");
      setDatasources(data.items);
    } catch {
      // 数据源接口可能不存在，忽略
    }
  };

  const loadLogs = async (configId?: number) => {
    try {
      const params = configId ? `?config_id=${configId}` : "?limit=50";
      const data = await apiFetch<SyncLogList>(`sync/logs${params}`);
      setLogs(data.items);
    } catch {
      showToast("加载同步日志失败", "error");
    }
  };

  const showToast = (message: string, severity: ToastState["severity"] = "success") => {
    setToast({ message, severity });
  };

  const handleOpenCreate = () => {
    setEditingConfig(createEmptyConfig());
    setEditingId(null);
    setDialogOpen(true);
  };

  const handleOpenEdit = (config: SyncConfig) => {
    setEditingConfig({
      name: config.name,
      description: config.description,
      source_table: config.source_table,
      source_schema: config.source_schema,
      source_db_alias: config.source_db_alias,
      target_datasource_id: config.target_datasource_id,
      target_table: config.target_table,
      target_schema: config.target_schema,
      sync_mode: config.sync_mode,
      status: config.status,
      timestamp_field: config.timestamp_field,
      batch_size: config.batch_size,
      field_mappings: config.field_mappings.map((m) => ({
        source_field: m.source_field,
        target_field: m.target_field,
        mapping_type: m.mapping_type,
        fixed_value: m.fixed_value,
        is_pk: m.is_pk,
      })),
    });
    setEditingId(config.id);
    setDialogOpen(true);
  };

  const handleCloseDialog = () => {
    setDialogOpen(false);
    setEditingConfig(null);
    setEditingId(null);
  };

  const handleSave = async () => {
    if (!editingConfig) return;
    if (!editingConfig.name.trim()) {
      showToast("请填写配置名称", "warning");
      return;
    }
    if (!editingConfig.source_table.trim()) {
      showToast("请填写源表名", "warning");
      return;
    }
    if (!editingConfig.target_datasource_id) {
      showToast("请选择目标数据源", "warning");
      return;
    }
    if (!editingConfig.target_table.trim()) {
      showToast("请填写目标表名", "warning");
      return;
    }
    // 校验至少一个有效字段映射
    const validMappings = editingConfig.field_mappings.filter(
      (m) => m.source_field.trim() || m.mapping_type === "constant"
    );
    if (validMappings.length === 0) {
      showToast("请添加至少一个有效字段映射", "warning");
      return;
    }

    try {
      if (editingId) {
        await apiFetch<SyncConfig>(`sync/configs/${editingId}`, {
          method: "PATCH",
          body: JSON.stringify(editingConfig),
        });
        showToast("同步配置已更新");
      } else {
        await apiFetch<SyncConfig>("sync/configs", {
          method: "POST",
          body: JSON.stringify(editingConfig),
        });
        showToast("同步配置已创建");
      }
      handleCloseDialog();
      loadConfigs();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "保存失败", "error");
    }
  };

  const handleDelete = async (id: number) => {
    if (!window.confirm("确定删除此同步配置？关联的日志将保留。")) return;
    try {
      await apiFetch<{ detail: string }>(`sync/configs/${id}`, {
        method: "DELETE",
      });
      showToast("同步配置已删除");
      loadConfigs();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "删除失败", "error");
    }
  };

  const handleTriggerSync = async (id: number) => {
    const forceFull = window.confirm("增量同步点取消 = 强制全量同步，确定执行？");
    try {
      const result = await apiFetch<SyncResult>(`sync/configs/${id}/trigger`, {
        method: "POST",
        body: JSON.stringify({ confirm: true, force_full: !forceFull }),
      });
      showToast(
        `同步${result.status === "success" ? "成功" : "失败"}：读取 ${result.rows_read}，写入 ${result.rows_written}，跳过 ${result.rows_skipped}`,
        result.status === "success" ? "success" : "error"
      );
      loadConfigs();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "同步触发失败", "error");
    }
  };

  const handleViewLogs = (configId: number) => {
    setViewingLogsConfigId(configId);
    loadLogs(configId);
    setLogsOpen(true);
  };

  // 编辑时添加字段映射
  const addMapping = () => {
    if (!editingConfig) return;
    setEditingConfig({
      ...editingConfig,
      field_mappings: [...editingConfig.field_mappings, emptyMapping()],
    });
  };

  const removeMapping = (index: number) => {
    if (!editingConfig) return;
    const mappings = [...editingConfig.field_mappings];
    mappings.splice(index, 1);
    setEditingConfig({ ...editingConfig, field_mappings: mappings });
  };

  const updateMapping = (index: number, updates: Partial<SyncFieldMapping>) => {
    if (!editingConfig) return;
    const mappings = [...editingConfig.field_mappings];
    mappings[index] = { ...mappings[index], ...updates };
    setEditingConfig({ ...editingConfig, field_mappings: mappings });
  };

  const getDatasourceName = (id: number) =>
    datasources.find((d) => d.id === id)?.name || `#${id}`;

  return (
    <Box sx={{ p: 3, maxWidth: 1400, mx: "auto" }}>
      <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 3 }}>
        <Typography variant="h4" component="h1">
          数据同步
        </Typography>
        <Button variant="contained" startIcon={<AddIcon />} onClick={handleOpenCreate}>
          新建同步配置
        </Button>
      </Stack>

      {configs.length === 0 ? (
        <Card sx={{ p: 4, textAlign: "center" }}>
          <Typography variant="body1" color="text.secondary">
            暂无同步配置，点击右上角按钮创建第一个配置
          </Typography>
        </Card>
      ) : (
        <TableContainer component={Paper}>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>名称</TableCell>
                <TableCell>源表</TableCell>
                <TableCell>目标数据源</TableCell>
                <TableCell>目标表</TableCell>
                <TableCell>模式</TableCell>
                <TableCell>状态</TableCell>
                <TableCell>最近同步</TableCell>
                <TableCell align="right">操作</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {configs.map((config) => (
                <TableRow key={config.id} hover>
                  <TableCell>
                    <Tooltip title={config.description}>
                      <Typography fontWeight={500}>{config.name}</Typography>
                    </Tooltip>
                  </TableCell>
                  <TableCell>
                    <Chip
                      label={config.source_schema ? `${config.source_schema}.${config.source_table}` : config.source_table}
                      size="small"
                      variant="outlined"
                    />
                  </TableCell>
                  <TableCell>{getDatasourceName(config.target_datasource_id)}</TableCell>
                  <TableCell>{config.target_schema ? `${config.target_schema}.${config.target_table}` : config.target_table}</TableCell>
                  <TableCell>
                    <Chip
                      label={config.sync_mode === "full" ? "全量" : "增量"}
                      size="small"
                      color={config.sync_mode === "full" ? "primary" : "info"}
                    />
                  </TableCell>
                  <TableCell>
                    <Chip
                      label={
                        config.status === "active" ? "启用" : config.status === "paused" ? "暂停" : "错误"
                      }
                      size="small"
                      color={
                        config.status === "active" ? "success" : config.status === "paused" ? "default" : "error"
                      }
                    />
                  </TableCell>
                  <TableCell>
                    {config.last_sync_at
                      ? new Date(config.last_sync_at).toLocaleString("zh-CN")
                      : "-"}
                  </TableCell>
                  <TableCell align="right">
                    <Stack direction="row" spacing={0}>
                      <Tooltip title="查看日志">
                        <IconButton size="small" onClick={() => handleViewLogs(config.id)}>
                          <VisibilityIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                      <Tooltip title="执行同步">
                        <IconButton size="small" color="success" onClick={() => handleTriggerSync(config.id)}>
                          <PlayArrowIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                      <Tooltip title="编辑">
                        <IconButton size="small" onClick={() => handleOpenEdit(config)}>
                          <EditIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                      <Tooltip title="删除">
                        <IconButton size="small" color="error" onClick={() => handleDelete(config.id)}>
                          <DeleteIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                    </Stack>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}

      {/* 创建/编辑对话框 */}
      <Dialog open={dialogOpen} onClose={handleCloseDialog} maxWidth="lg" fullWidth>
        <DialogTitle>{editingId ? "编辑同步配置" : "新建同步配置"}</DialogTitle>
        <DialogContent>
          {editingConfig && (
            <Grid container spacing={2} sx={{ mt: 1 }}>
              <Grid item xs={12} sm={6}>
                <TextField
                  label="配置名称"
                  value={editingConfig.name}
                  onChange={(e) => setEditingConfig({ ...editingConfig, name: e.target.value })}
                  fullWidth
                  required
                  size="small"
                />
              </Grid>
              <Grid item xs={12} sm={6}>
                <TextField
                  label="描述"
                  value={editingConfig.description || ""}
                  onChange={(e) => setEditingConfig({ ...editingConfig, description: e.target.value })}
                  fullWidth
                  size="small"
                />
              </Grid>

              {/* 源表区域 */}
              <Grid item xs={12}>
                <Typography variant="subtitle2" sx={{ mt: 1, mb: 1 }}>源表（rdbase 平台库）</Typography>
              </Grid>
              <Grid item xs={12} sm={4}>
                <TextField
                  label="源 Schema"
                  value={editingConfig.source_schema || ""}
                  onChange={(e) => setEditingConfig({ ...editingConfig, source_schema: e.target.value })}
                  fullWidth
                  size="small"
                  placeholder="留空使用默认 schema"
                />
              </Grid>
              <Grid item xs={12} sm={4}>
                <TextField
                  label="源表名"
                  value={editingConfig.source_table}
                  onChange={(e) => setEditingConfig({ ...editingConfig, source_table: e.target.value })}
                  fullWidth
                  required
                  size="small"
                />
              </Grid>
              <Grid item xs={12} sm={4}>
                <FormControl fullWidth size="small">
                  <InputLabel>源库 Alias</InputLabel>
                  <Select
                    value={editingConfig.source_db_alias}
                    onChange={(e) => setEditingConfig({ ...editingConfig, source_db_alias: e.target.value })}
                    label="源库 Alias"
                  >
                    <MenuItem value="default">default</MenuItem>
                    <MenuItem value="readonly">readonly</MenuItem>
                  </Select>
                </FormControl>
              </Grid>

              {/* 目标区域 */}
              <Grid item xs={12}>
                <Typography variant="subtitle2" sx={{ mt: 1, mb: 1 }}>目标表（外部数据源）</Typography>
              </Grid>
              <Grid item xs={12} sm={4}>
                <FormControl fullWidth size="small" required>
                  <InputLabel>目标数据源</InputLabel>
                  <Select
                    value={editingConfig.target_datasource_id || ""}
                    onChange={(e) => setEditingConfig({ ...editingConfig, target_datasource_id: Number(e.target.value) })}
                    label="目标数据源"
                  >
                    {datasources.map((ds) => (
                      <MenuItem key={ds.id} value={ds.id}>
                        {ds.name} ({ds.engine})
                      </MenuItem>
                    ))}
                  </Select>
                </FormControl>
              </Grid>
              <Grid item xs={12} sm={4}>
                <TextField
                  label="目标 Schema"
                  value={editingConfig.target_schema || ""}
                  onChange={(e) => setEditingConfig({ ...editingConfig, target_schema: e.target.value })}
                  fullWidth
                  size="small"
                  placeholder="留空使用默认 schema"
                />
              </Grid>
              <Grid item xs={12} sm={4}>
                <TextField
                  label="目标表名"
                  value={editingConfig.target_table}
                  onChange={(e) => setEditingConfig({ ...editingConfig, target_table: e.target.value })}
                  fullWidth
                  required
                  size="small"
                />
              </Grid>

              {/* 同步参数 */}
              <Grid item xs={12}>
                <Typography variant="subtitle2" sx={{ mt: 1, mb: 1 }}>同步参数</Typography>
              </Grid>
              <Grid item xs={12} sm={3}>
                <FormControl fullWidth size="small">
                  <InputLabel>同步模式</InputLabel>
                  <Select
                    value={editingConfig.sync_mode}
                    onChange={(e) => setEditingConfig({ ...editingConfig, sync_mode: e.target.value })}
                    label="同步模式"
                  >
                    <MenuItem value="incremental">增量</MenuItem>
                    <MenuItem value="full">全量</MenuItem>
                  </Select>
                </FormControl>
              </Grid>
              <Grid item xs={12} sm={3}>
                <FormControl fullWidth size="small">
                  <InputLabel>状态</InputLabel>
                  <Select
                    value={editingConfig.status}
                    onChange={(e) => setEditingConfig({ ...editingConfig, status: e.target.value })}
                    label="状态"
                  >
                    <MenuItem value="active">启用</MenuItem>
                    <MenuItem value="paused">暂停</MenuItem>
                  </Select>
                </FormControl>
              </Grid>
              <Grid item xs={12} sm={3}>
                <TextField
                  label="时间戳字段"
                  value={editingConfig.timestamp_field}
                  onChange={(e) => setEditingConfig({ ...editingConfig, timestamp_field: e.target.value })}
                  fullWidth
                  size="small"
                  helperText="增量同步以此字段筛选变更行"
                />
              </Grid>
              <Grid item xs={12} sm={3}>
                <TextField
                  label="批大小"
                  type="number"
                  value={editingConfig.batch_size}
                  onChange={(e) => setEditingConfig({ ...editingConfig, batch_size: Number(e.target.value) })}
                  fullWidth
                  size="small"
                />
              </Grid>

              {/* 字段映射 */}
              <Grid item xs={12}>
                <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mt: 2, mb: 1 }}>
                  <Typography variant="subtitle2">字段映射</Typography>
                  <Button size="small" startIcon={<AddIcon />} onClick={addMapping}>
                    添加映射
                  </Button>
                </Stack>
              </Grid>
              <Grid item xs={12}>
                <TableContainer component={Paper} variant="outlined">
                  <Table size="small">
                    <TableHead>
                      <TableRow>
                        <TableCell>源字段</TableCell>
                        <TableCell>映射类型</TableCell>
                        <TableCell>常量值</TableCell>
                        <TableCell>目标字段</TableCell>
                        <TableCell>主键</TableCell>
                        <TableCell align="right">操作</TableCell>
                      </TableRow>
                    </TableHead>
                    <TableBody>
                      {editingConfig.field_mappings.map((m, idx) => (
                        <TableRow key={idx}>
                          <TableCell>
                            <TextField
                              value={m.source_field}
                              onChange={(e) => updateMapping(idx, { source_field: e.target.value })}
                              size="small"
                              disabled={m.mapping_type === "constant"}
                              placeholder="源字段名"
                              sx={{ width: 140 }}
                            />
                          </TableCell>
                          <TableCell>
                            <FormControl size="small" sx={{ minWidth: 100 }}>
                              <Select
                                value={m.mapping_type}
                                onChange={(e) => updateMapping(idx, { mapping_type: e.target.value as "direct" | "constant" })}
                              >
                                <MenuItem value="direct">直接映射</MenuItem>
                                <MenuItem value="constant">常量</MenuItem>
                              </Select>
                            </FormControl>
                          </TableCell>
                          <TableCell>
                            <TextField
                              value={m.fixed_value}
                              onChange={(e) => updateMapping(idx, { fixed_value: e.target.value })}
                              size="small"
                              disabled={m.mapping_type !== "constant"}
                              placeholder="常量值"
                              sx={{ width: 120 }}
                            />
                          </TableCell>
                          <TableCell>
                            <TextField
                              value={m.target_field}
                              onChange={(e) => updateMapping(idx, { target_field: e.target.value })}
                              size="small"
                              placeholder="目标字段名"
                              sx={{ width: 140 }}
                            />
                          </TableCell>
                          <TableCell>
                            <FormControl size="small">
                              <Select
                                value={m.is_pk ? "true" : "false"}
                                onChange={(e) => updateMapping(idx, { is_pk: e.target.value === "true" })}
                                sx={{ minWidth: 70 }}
                              >
                                <MenuItem value="true">是</MenuItem>
                                <MenuItem value="false">否</MenuItem>
                              </Select>
                            </FormControl>
                          </TableCell>
                          <TableCell align="right">
                            <IconButton size="small" color="error" onClick={() => removeMapping(idx)}>
                              <DeleteIcon fontSize="small" />
                            </IconButton>
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </TableContainer>
              </Grid>
            </Grid>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={handleCloseDialog}>取消</Button>
          <Button variant="contained" onClick={handleSave}>
            保存
          </Button>
        </DialogActions>
      </Dialog>

      {/* 日志对话框 */}
      <Dialog open={logsOpen} onClose={() => setLogsOpen(false)} maxWidth="md" fullWidth>
        <DialogTitle>
          同步日志
          {viewingLogsConfigId && (
            <Typography variant="caption" color="text.secondary" sx={{ ml: 1 }}>
              （仅显示此配置）
            </Typography>
          )}
        </DialogTitle>
        <DialogContent>
          <Stack direction="row" justifyContent="flex-end" sx={{ mb: 1 }}>
            <Button
              size="small"
              startIcon={<RefresherIcon />}
              onClick={() => loadLogs(viewingLogsConfigId ?? undefined)}
            >
              刷新
            </Button>
          </Stack>
          <TableContainer component={Paper} variant="outlined">
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>状态</TableCell>
                  <TableCell>模式</TableCell>
                  <TableCell align="right">读取</TableCell>
                  <TableCell align="right">写入</TableCell>
                  <TableCell align="right">跳过</TableCell>
                  <TableCell align="right">耗时(ms)</TableCell>
                  <TableCell>开始时间</TableCell>
                  <TableCell>错误</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {logs.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={8} align="center" sx={{ py: 3, color: "text.secondary" }}>
                      暂无日志记录
                    </TableCell>
                  </TableRow>
                ) : (
                  logs.map((log) => (
                    <TableRow key={log.id}>
                      <TableCell>
                        <Chip
                          label={log.status === "success" ? "成功" : log.status === "failed" ? "失败" : "执行中"}
                          size="small"
                          color={log.status === "success" ? "success" : log.status === "failed" ? "error" : "warning"}
                        />
                      </TableCell>
                      <TableCell>
                        <Chip label={log.mode === "full" ? "全量" : "增量"} size="small" variant="outlined" />
                      </TableCell>
                      <TableCell align="right">{log.rows_read}</TableCell>
                      <TableCell align="right">{log.rows_written}</TableCell>
                      <TableCell align="right">{log.rows_skipped}</TableCell>
                      <TableCell align="right">{log.duration_ms}</TableCell>
                      <TableCell>{new Date(log.started_at).toLocaleString("zh-CN")}</TableCell>
                      <TableCell sx={{ maxWidth: 200, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {log.error_message || "-"}
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </TableContainer>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setLogsOpen(false)}>关闭</Button>
        </DialogActions>
      </Dialog>

      {/* Toast */}
      <Snackbar
        open={!!toast}
        autoHideDuration={4000}
        onClose={() => setToast(null)}
        anchorOrigin={{ vertical: "bottom", horizontal: "right" }}
      >
        {toast && (
          <Alert severity={toast.severity} onClose={() => setToast(null)} variant="filled">
            {toast.message}
          </Alert>
        )}
      </Snackbar>
    </Box>
  );
}
