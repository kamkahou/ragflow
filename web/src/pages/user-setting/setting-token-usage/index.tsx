import { useFetchUserInfo } from '@/hooks/user-setting-hooks';
import { getAuthorization } from '@/utils/authorization-util';
import {
  BarChartOutlined,
  EditOutlined,
  ReloadOutlined,
  SyncOutlined,
} from '@ant-design/icons';
import {
  App,
  Button,
  Card,
  Form,
  Input,
  InputNumber,
  Modal,
  Progress,
  Select,
  Space,
  Spin,
  Table,
  Typography,
} from 'antd';
import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';

const { Text } = Typography;

interface TokenUsageRecord {
  user_id?: string;
  user_email?: string;
  llm_type: string;
  llm_name: string;
  used_tokens: number;
  token_limit: number;
  reset_date: string;
  is_active: boolean;
  create_date: string;
  update_date: string;
}

interface TokenStatistics {
  total_users: number;
  active_users: number;
  total_tokens_used: number;
  total_tokens_limit: number;
  users_over_limit: number;
}

const TokenUsageSettings: React.FC = () => {
  const { t } = useTranslation();
  const { data: userInfo } = useFetchUserInfo();
  const { message } = App.useApp();
  const [loading, setLoading] = useState(false);
  const [tokenUsageData, setTokenUsageData] = useState<TokenUsageRecord[]>([]);
  const [statistics, setStatistics] = useState<TokenStatistics | null>(null);
  const [editModalVisible, setEditModalVisible] = useState(false);
  const [resetModalVisible, setResetModalVisible] = useState(false);
  const [editingRecord, setEditingRecord] = useState<TokenUsageRecord | null>(
    null,
  );
  const [form] = Form.useForm();

  const isAdmin = userInfo?.is_superuser;

  const fetchTokenUsage = async () => {
    setLoading(true);
    try {
      const endpoint = isAdmin
        ? '/v1/user/admin/token_usage/users'
        : '/v1/user/token_usage';
      console.log('Fetching token usage from endpoint:', endpoint);

      const response = await fetch(endpoint, {
        method: 'GET',
        headers: {
          'Content-Type': 'application/json',
          Authorization: getAuthorization(),
        },
        credentials: 'include',
      });

      console.log('Response status:', response.status);
      console.log('Response ok:', response.ok);
      console.log(
        'Response headers:',
        Object.fromEntries(response.headers.entries()),
      );

      if (response.ok) {
        const result = await response.json();
        console.log('Response data:', result);
        console.log('Token usage data array:', result.data);
        console.log(
          'Number of users returned:',
          result.data ? result.data.length : 0,
        );

        // 詳細查看每個用戶的數據結構
        if (result.data && result.data.length > 0) {
          result.data.forEach((user, index) => {
            console.log(`User ${index + 1}:`, user);
            console.log(`User ${index + 1} email:`, user.email);
            console.log(`User ${index + 1} user_email:`, user.user_email);
          });
        }

        if (result.code === 0) {
          setTokenUsageData(result.data || []);
        } else {
          console.error('API returned error:', result);
          message.error(result.message || '獲取 token 使用數據失敗');
        }
      } else {
        const errorText = await response.text();
        console.error('HTTP Error:', response.status, response.statusText);
        console.error('Error response body:', errorText);
        message.error(`獲取 token 使用數據失敗 (HTTP ${response.status})`);
      }
    } catch (error) {
      console.error('Error fetching token usage:', error);
      message.error('網絡錯誤');
    } finally {
      setLoading(false);
    }
  };

  const fetchStatistics = async () => {
    if (!isAdmin) return;

    try {
      console.log(
        'Fetching statistics from: /v1/user/admin/token_usage/statistics',
      );
      const response = await fetch('/v1/user/admin/token_usage/statistics', {
        method: 'GET',
        headers: {
          'Content-Type': 'application/json',
          Authorization: getAuthorization(),
        },
        credentials: 'include',
      });

      console.log('Statistics response status:', response.status);
      console.log('Statistics response ok:', response.ok);

      if (response.ok) {
        const result = await response.json();
        console.log('Statistics response data:', result);
        console.log('Statistics data object:', result.data);
        if (result.code === 0) {
          setStatistics(result.data);
        } else {
          console.error('Statistics API returned error:', result);
        }
      } else {
        const errorText = await response.text();
        console.error(
          'Statistics HTTP Error:',
          response.status,
          response.statusText,
        );
        console.error('Statistics error response body:', errorText);
      }
    } catch (error) {
      console.error('Error fetching statistics:', error);
    }
  };

  useEffect(() => {
    fetchTokenUsage();
    if (isAdmin) {
      fetchStatistics();
    }
  }, [isAdmin]);

  const handleEditLimit = (record: TokenUsageRecord) => {
    setEditingRecord(record);
    form.setFieldsValue({
      llm_type: record.llm_type,
      llm_name: record.llm_name,
      token_limit: record.token_limit,
      is_active: record.is_active,
    });
    setEditModalVisible(true);
  };

  const handleResetUsage = (record: TokenUsageRecord) => {
    setEditingRecord(record);
    setResetModalVisible(true);
  };

  const confirmEditLimit = async () => {
    try {
      const values = await form.validateFields();
      const response = await fetch('/v1/user/token_usage/set_limit', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: getAuthorization(),
        },
        credentials: 'include',
        body: JSON.stringify({
          user_id: editingRecord?.user_id,
          llm_type: values.llm_type,
          llm_name: values.llm_name,
          token_limit: values.token_limit,
        }),
      });

      if (response.ok) {
        const result = await response.json();
        if (result.code === 0) {
          message.success('限制設置成功');
          setEditModalVisible(false);
          fetchTokenUsage();
          if (isAdmin) fetchStatistics();
        } else {
          message.error(result.message || '設置失敗');
        }
      } else {
        message.error('設置失敗');
      }
    } catch (error) {
      console.error('Error setting limit:', error);
      message.error('網絡錯誤');
    }
  };

  const confirmResetUsage = async () => {
    try {
      const response = await fetch('/v1/user/token_usage/reset', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: getAuthorization(),
        },
        credentials: 'include',
        body: JSON.stringify({
          user_id: editingRecord?.user_id,
          llm_type: editingRecord?.llm_type,
          llm_name: editingRecord?.llm_name,
        }),
      });

      if (response.ok) {
        const result = await response.json();
        if (result.code === 0) {
          message.success('重置成功');
          setResetModalVisible(false);
          fetchTokenUsage();
          if (isAdmin) fetchStatistics();
        } else {
          message.error(result.message || '重置失敗');
        }
      } else {
        message.error('重置失敗');
      }
    } catch (error) {
      console.error('Error resetting usage:', error);
      message.error('網絡錯誤');
    }
  };

  const getUsagePercentage = (used: number, limit: number) => {
    if (limit === 0) return 0;
    return Math.min((used / limit) * 100, 100);
  };

  const getStatusColor = (used: number, limit: number) => {
    if (limit === 0) return '#52c41a';
    const percentage = (used / limit) * 100;
    if (percentage >= 90) return '#ff4d4f';
    if (percentage >= 75) return '#faad14';
    return '#52c41a';
  };

  const formatNumber = (num: number) => {
    if (num == null || num === undefined) {
      return '0';
    }
    if (num >= 1000000) {
      return `${(num / 1000000).toFixed(1)}M`;
    }
    if (num >= 1000) {
      return `${(num / 1000).toFixed(1)}K`;
    }
    return num.toString();
  };

  const baseColumns = [
    {
      title: t('llmType'),
      dataIndex: 'llm_type',
      key: 'llm_type',
      width: 120,
      render: (type: string) => {
        const typeMap: { [key: string]: string } = {
          CHAT: t('chatModel'),
          EMBEDDING: t('embedding'),
          RERANK: t('rerank'),
          IMAGE2TEXT: t('image2text'),
          ASR: t('asr'),
          TTS: t('tts'),
        };
        return typeMap[type] || type;
      },
    },
    {
      title: t('tokenModelName'),
      dataIndex: 'llm_name',
      key: 'llm_name',
      width: 200,
    },
    {
      title: t('tokensUsed'),
      dataIndex: 'used_tokens',
      key: 'used_tokens',
      width: 120,
      render: (used: number) => formatNumber(used),
    },
    {
      title: t('tokenLimit'),
      dataIndex: 'token_limit',
      key: 'token_limit',
      width: 120,
      render: (limit: number) =>
        limit === 0 ? t('unlimited') : formatNumber(limit),
    },
    {
      title: t('usage'),
      key: 'usage',
      width: 200,
      render: (_: any, record: TokenUsageRecord) => {
        const percentage = getUsagePercentage(
          record.used_tokens,
          record.token_limit,
        );
        const color = getStatusColor(record.used_tokens, record.token_limit);

        return (
          <div>
            <Progress
              percent={record.token_limit === 0 ? 0 : percentage}
              strokeColor={color}
              size="small"
              showInfo={false}
            />
            <div style={{ fontSize: '12px', color: '#666', marginTop: '4px' }}>
              {record.token_limit === 0
                ? t('unlimited')
                : `${formatNumber(record.used_tokens)} / ${formatNumber(record.token_limit)}`}
            </div>
          </div>
        );
      },
    },
    {
      title: t('resetDate'),
      dataIndex: 'reset_date',
      key: 'reset_date',
      width: 120,
      render: (date: string) =>
        date ? new Date(date).toLocaleDateString() : '-',
    },
    {
      title: t('tokenStatus'),
      dataIndex: 'is_active',
      key: 'is_active',
      width: 80,
      render: (active: boolean) => (
        <span style={{ color: active ? '#52c41a' : '#999' }}>
          {active ? t('active') : t('inactive')}
        </span>
      ),
    },
  ];

  // 管理員額外的列
  const adminColumns = isAdmin
    ? [
        {
          title: t('userEmail'),
          dataIndex: 'user_email',
          key: 'user_email',
          width: 200,
          render: (email: string) => email || '-',
        },
        ...baseColumns,
        {
          title: t('tokenOperation'),
          key: 'action',
          width: 120,
          render: (_: any, record: TokenUsageRecord) => (
            <Space size="small">
              <Button
                type="link"
                size="small"
                icon={<EditOutlined />}
                onClick={() => handleEditLimit(record)}
              >
                {t('editLimit')}
              </Button>
              <Button
                type="link"
                size="small"
                icon={<SyncOutlined />}
                onClick={() => handleResetUsage(record)}
              >
                {t('resetUsage')}
              </Button>
            </Space>
          ),
        },
      ]
    : baseColumns;

  return (
    <div className="token-usage-settings">
      {/* 管理員統計卡片 */}
      {isAdmin && statistics && (
        <Card
          title={
            <Space>
              <BarChartOutlined />
              <span>{t('tokenStatistics')}</span>
            </Space>
          }
          style={{ marginBottom: '16px' }}
        >
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))',
              gap: '16px',
            }}
          >
            <div>
              <Text type="secondary">{t('totalUsers')}</Text>
              <div
                style={{
                  fontSize: '24px',
                  fontWeight: 'bold',
                  color: '#1890ff',
                }}
              >
                {statistics.total_users}
              </div>
            </div>
            <div>
              <Text type="secondary">{t('activeUsers')}</Text>
              <div
                style={{
                  fontSize: '24px',
                  fontWeight: 'bold',
                  color: '#52c41a',
                }}
              >
                {statistics.active_users}
              </div>
            </div>
            <div>
              <Text type="secondary">{t('totalTokensUsed')}</Text>
              <div
                style={{
                  fontSize: '24px',
                  fontWeight: 'bold',
                  color: '#faad14',
                }}
              >
                {formatNumber(statistics.total_tokens_used)}
              </div>
            </div>
            <div>
              <Text type="secondary">{t('totalTokensLimit')}</Text>
              <div
                style={{
                  fontSize: '24px',
                  fontWeight: 'bold',
                  color: '#722ed1',
                }}
              >
                {statistics.total_tokens_limit > 0
                  ? formatNumber(statistics.total_tokens_limit)
                  : t('unlimited')}
              </div>
            </div>
            <div>
              <Text type="secondary">{t('usersOverLimit')}</Text>
              <div
                style={{
                  fontSize: '24px',
                  fontWeight: 'bold',
                  color: '#ff4d4f',
                }}
              >
                {statistics.users_over_limit}
              </div>
            </div>
          </div>
        </Card>
      )}

      <Card
        title={
          <Space>
            <span>
              {isAdmin ? t('allUsersTokenUsage') : t('tokenUsageManagement')}
            </span>
            <Button
              type="text"
              icon={<ReloadOutlined />}
              onClick={() => {
                fetchTokenUsage();
                if (isAdmin) fetchStatistics();
              }}
              loading={loading}
              size="small"
            />
          </Space>
        }
        style={{ margin: '16px 0' }}
      >
        <div style={{ marginBottom: '16px', color: '#666', fontSize: '14px' }}>
          {isAdmin
            ? t('adminAllUsersTokenUsageDescription')
            : t('userTokenUsageDescription')}
        </div>

        <Spin spinning={loading}>
          <Table
            columns={adminColumns}
            dataSource={tokenUsageData}
            rowKey={(record) =>
              `${record.user_id || 'self'}-${record.llm_type}-${record.llm_name}`
            }
            pagination={{
              pageSize: 10,
              showSizeChanger: true,
              showQuickJumper: true,
              showTotal: (total, range) =>
                t('showingRecords', { start: range[0], end: range[1], total }),
            }}
            size="small"
            scroll={{ x: isAdmin ? 1200 : 800 }}
          />
        </Spin>

        {tokenUsageData.length === 0 && !loading && (
          <div
            style={{
              textAlign: 'center',
              padding: '40px',
              color: '#999',
              fontSize: '14px',
            }}
          >
            {t('noTokenUsageData')}
          </div>
        )}
      </Card>

      {/* 編輯限制模態框 */}
      <Modal
        title={t('editTokenLimit')}
        open={editModalVisible}
        onOk={confirmEditLimit}
        onCancel={() => setEditModalVisible(false)}
        width={500}
      >
        <Form form={form} layout="vertical">
          <Form.Item name="llm_type" label={t('llmType')}>
            <Input disabled />
          </Form.Item>
          <Form.Item name="llm_name" label={t('tokenModelName')}>
            <Input disabled />
          </Form.Item>
          <Form.Item
            name="token_limit"
            label={t('tokenLimit')}
            rules={[{ required: true, message: t('tokenLimitRequired') }]}
          >
            <InputNumber
              min={0}
              style={{ width: '100%' }}
              placeholder={t('zeroForUnlimited')}
            />
          </Form.Item>
          <Form.Item name="is_active" label={t('status')}>
            <Select>
              <Select.Option value={true}>{t('active')}</Select.Option>
              <Select.Option value={false}>{t('inactive')}</Select.Option>
            </Select>
          </Form.Item>
        </Form>
      </Modal>

      {/* 重置使用量確認模態框 */}
      <Modal
        title={t('confirmResetUsage')}
        open={resetModalVisible}
        onOk={confirmResetUsage}
        onCancel={() => setResetModalVisible(false)}
        okText={t('confirm')}
        cancelText={t('cancel')}
      >
        <p>{t('resetUsageConfirmMessage')}</p>
        {editingRecord && (
          <div
            style={{
              marginTop: '16px',
              padding: '12px',
              backgroundColor: '#f5f5f5',
              borderRadius: '4px',
            }}
          >
            <p>
              <strong>{t('userEmail')}:</strong>{' '}
              {editingRecord.user_email || t('currentUser')}
            </p>
            <p>
              <strong>{t('llmType')}:</strong> {editingRecord.llm_type}
            </p>
            <p>
              <strong>{t('tokenModelName')}:</strong> {editingRecord.llm_name}
            </p>
            <p>
              <strong>{t('currentUsage')}:</strong>{' '}
              {formatNumber(editingRecord.used_tokens)}
            </p>
          </div>
        )}
      </Modal>
    </div>
  );
};

export default TokenUsageSettings;
