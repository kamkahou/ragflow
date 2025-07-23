import { useFetchUserInfo } from '@/hooks/user-setting-hooks';
import { getAuthorization } from '@/utils/authorization-util';
import { App, Progress, Space, Spin, Typography } from 'antd';
import React, { useEffect, useState } from 'react';
import { useTranslation } from 'react-i18next';
import styles from './index.less';

const { Text } = Typography;

interface TokenUsageRecord {
  llm_type: string;
  llm_name: string;
  used_tokens: number;
  token_limit: number;
  reset_date: string;
  is_active: boolean;
}

interface TokenUsageBarProps {
  inline?: boolean; // 是否為內聯模式
}

const TokenUsageBar: React.FC<TokenUsageBarProps> = ({ inline = false }) => {
  const { t } = useTranslation();
  const { data: userInfo } = useFetchUserInfo();
  const { message } = App.useApp();
  const [tokenUsageData, setTokenUsageData] = useState<TokenUsageRecord[]>([]);
  const [loading, setLoading] = useState(false);

  console.log('TokenUsageBar: 組件初始化, userInfo:', userInfo);

  // 調試：總是顯示 token 使用條以便測試
  // TODO: 恢復只對普通用戶顯示的邏輯
  // if (userInfo?.is_superuser) {
  //   return null;
  // }

  const fetchTokenUsage = async () => {
    setLoading(true);
    console.log('TokenUsageBar: 開始獲取 token 使用數據...');
    try {
      const response = await fetch('/v1/user/token_usage', {
        method: 'GET',
        headers: {
          'Content-Type': 'application/json',
          Authorization: getAuthorization(),
        },
        credentials: 'include',
      });

      console.log('TokenUsageBar: API 響應狀態:', response.status);

      if (response.ok) {
        const result = await response.json();
        console.log('TokenUsageBar: API 響應數據:', result);
        if (result.retcode === 0 || result.code === 0) {
          setTokenUsageData(result.data || []);
          console.log('TokenUsageBar: 設置 token 數據:', result.data);
        }
      } else {
        console.error(
          'TokenUsageBar: API 響應失敗:',
          response.status,
          response.statusText,
        );
      }
    } catch (error) {
      console.error('TokenUsageBar: 獲取 token 使用數據時出錯:', error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchTokenUsage();
  }, []);

  const formatNumber = (num: number) => {
    if (num >= 1000000) {
      return `${(num / 1000000).toFixed(1)}M`;
    }
    if (num >= 1000) {
      return `${(num / 1000).toFixed(1)}K`;
    }
    return num.toString();
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

  const getRemainingTokens = (used: number, limit: number) => {
    if (limit === 0) return 'unlimited';
    return Math.max(0, limit - used);
  };

  // 找到聊天類型的 token 使用情況
  const chatUsage = tokenUsageData.find(
    (item) => item.llm_type === 'CHAT' || item.llm_type === 'chat',
  );

  console.log(
    'TokenUsageBar: 渲染狀態 - loading:',
    loading,
    'chatUsage:',
    chatUsage,
    'tokenUsageData:',
    tokenUsageData,
  );

  if (loading) {
    return inline ? (
      <Text type="secondary" style={{ fontSize: '11px' }}>
        載入中...
      </Text>
    ) : (
      <div className={styles.tokenUsageBar}>
        <Spin size="small" />
        <Text type="secondary" style={{ marginLeft: 8 }}>
          正在載入 Token 使用情況...
        </Text>
      </div>
    );
  }

  if (!chatUsage) {
    // 即使沒有數據也顯示默認狀態
    return inline ? (
      <Text type="secondary" style={{ fontSize: '11px' }}>
        {tokenUsageData.length > 0 ? '無 CHAT 數據' : '載入中...'}
      </Text>
    ) : (
      <div className={styles.tokenUsageBar} style={{ border: '2px solid red' }}>
        <Space size="small" align="center" style={{ width: '100%' }}>
          <Text type="secondary" style={{ fontSize: '12px', minWidth: '80px' }}>
            剩餘 Token:
          </Text>
          <Progress
            percent={0}
            strokeColor="#52c41a"
            size="small"
            showInfo={false}
            style={{ flex: 1, minWidth: '100px' }}
          />
          <Text
            style={{
              fontSize: '12px',
              color: '#52c41a',
              fontWeight: '500',
              minWidth: '120px',
            }}
          >
            {tokenUsageData.length > 0 ? '數據無 CHAT 類型' : '載入中...'}
          </Text>
        </Space>
      </div>
    );
  }

  const percentage = getUsagePercentage(
    chatUsage.used_tokens,
    chatUsage.token_limit,
  );
  const color = getStatusColor(chatUsage.used_tokens, chatUsage.token_limit);
  const remainingTokens = getRemainingTokens(
    chatUsage.used_tokens,
    chatUsage.token_limit,
  );

  if (inline) {
    // 內聯模式：簡潔顯示，保留進度條
    return (
      <Space size={4} align="center" style={{ flex: 1 }}>
        <Text type="secondary" style={{ fontSize: '11px', minWidth: '30px' }}>
          剩餘:
        </Text>
        <Progress
          percent={chatUsage.token_limit === 0 ? 0 : percentage}
          strokeColor={color}
          size="small"
          showInfo={false}
          style={{ flex: 1, minWidth: '60px', maxWidth: '100px' }}
        />
        <Text
          style={{
            fontSize: '11px',
            color: color,
            fontWeight: '500',
            minWidth: '40px',
          }}
        >
          {chatUsage.token_limit === 0
            ? '無限制'
            : typeof remainingTokens === 'number'
              ? `${formatNumber(remainingTokens)}`
              : remainingTokens}
        </Text>
      </Space>
    );
  }

  return (
    <div className={styles.tokenUsageBar}>
      <Space size="small" align="center" style={{ width: '100%' }}>
        <Text type="secondary" style={{ fontSize: '12px', minWidth: '80px' }}>
          剩餘 Token:
        </Text>
        <Progress
          percent={chatUsage.token_limit === 0 ? 0 : percentage}
          strokeColor={color}
          size="small"
          showInfo={false}
          style={{ flex: 1, minWidth: '100px' }}
        />
        <Text
          style={{
            fontSize: '12px',
            color: color,
            fontWeight: '500',
            minWidth: '120px',
          }}
        >
          {chatUsage.token_limit === 0
            ? '無限制'
            : typeof remainingTokens === 'number'
              ? `${formatNumber(remainingTokens)}`
              : remainingTokens}
        </Text>
        {chatUsage.reset_date && (
          <Text type="secondary" style={{ fontSize: '11px' }}>
            重置: {new Date(chatUsage.reset_date).toLocaleDateString()}
          </Text>
        )}
      </Space>
    </div>
  );
};

export default TokenUsageBar;
