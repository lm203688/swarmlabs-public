/**
 * SwarmLabs Differential Privacy Utilities
 * PI-2026-003: 差分隐私在聚合统计中的应用
 * 
 * 在用户行为聚合统计API中加入拉普拉斯噪声(epsilon=1.0)，
 * 确保无法通过聚合数据反推个体用户信息。
 * 
 * 用途：
 * - 积分经济报告中的用户行为统计匿名化
 * - 实验数据聚合统计（平均参数、成功率等）
 * - 仪表盘展示的汇总指标
 * 
 * 注意：此为纯前端/Edge Worker实现，无需外部依赖
 */

const DifferentialPrivacy = (() => {
  'use strict';

  /**
   * 生成拉普拉斯噪声
   * Laplace(b) = -b * ln(U) 其中 U ~ Uniform(0,1)
   * @param {number} scale - 拉普拉斯分布的尺度参数 b = sensitivity / epsilon
   * @returns {number} 噪声值
   */
  function _laplaceSample(scale) {
    // 对称拉普拉斯采样（逆CDF法），均值=0
    const u = Math.random() - 0.5; // uniform in [-0.5, 0.5)
    return -scale * Math.sign(u) * Math.log(1 - 2 * Math.abs(u));
  }

  /**
   * 计算敏感性 (sensitivity)
   * 对于计数查询（bounded differential privacy）：sensitivity = 1
   * 对于求和查询：sensitivity = max_contribution
   * 对于平均值：sensitivity = max_value / min_participants
   * @param {string} queryType - 查询类型: 'count' | 'sum' | 'average' | 'histogram'
   * @param {object} options - 额外参数
   * @returns {number} 敏感性值
   */
  function _computeSensitivity(queryType, options = {}) {
    switch (queryType) {
      case 'count':
        return 1; // 单个用户最多改变计数1
      case 'sum':
        return options.maxContribution || 1; // 单个用户最大贡献
      case 'average':
        // 平均值的敏感性 = max_value / min_participants
        const maxVal = options.maxValue || 100;
        const minParticipants = Math.max(options.minParticipants || 1, 1);
        return maxVal / minParticipants;
      case 'histogram':
        return 1; // 单个用户最多改变一个bin
      default:
        return 1;
    }
  }

  /**
   * 对单个数值添加差分隐私噪声
   * @param {number} trueValue - 真实聚合值
   * @param {number} epsilon - 隐私预算 (越小越隐私，越大越精确)。推荐 0.5-2.0
   * @param {string} queryType - 查询类型
   * @param {object} options - 敏感性计算参数
   * @returns {number} 加噪后的值
   */
  function addNoise(trueValue, epsilon = 1.0, queryType = 'count', options = {}) {
    if (epsilon <= 0) {
      console.warn('[DP] epsilon must be positive, using trueValue');
      return trueValue;
    }

    const sensitivity = _computeSensitivity(queryType, options);
    const scale = sensitivity / epsilon;
    const noise = _laplaceSample(scale);
    const noisyValue = trueValue + noise;

    // 对于计数等非负值，clamp到0
    if (queryType === 'count' || queryType === 'histogram') {
      return Math.max(0, Math.round(noisyValue));
    }

    // 对于货币等精确场景，保留2位小数
    if (options.precision !== undefined) {
      return Number(noisyValue.toFixed(options.precision));
    }

    return noisyValue;
  }

  /**
   * 对直方图/分组统计添加差分隐私噪声
   * @param {object} histogram - { bin1: count1, bin2: count2, ... }
   * @param {number} epsilon - 总隐私预算（会均分到所有bin）
   * @returns {object} 加噪后的直方图
   */
  function anonymizeHistogram(histogram, epsilon = 1.0) {
    const bins = Object.keys(histogram);
    const perBinEpsilon = epsilon / bins.length;
    const result = {};

    for (const bin of bins) {
      result[bin] = addNoise(histogram[bin], perBinEpsilon, 'histogram');
    }

    return result;
  }

  /**
   * 对用户行为统计报告进行匿名化处理
   * 典型输入: { totalExperiments: 150, activeUsers: 42, avgCreditsUsed: 35.2, ... }
   * @param {object} report - 统计报告对象
   * @param {number} epsilon - 隐私预算
   * @returns {object} 匿名化后的报告（数值字段加噪，非数值字段保留）
   */
  function anonymizeReport(report, epsilon = 1.0) {
    const dpConfig = {
      // 计数类：epsilon=1.0, sensitivity=1
      totalExperiments:    { type: 'count', eps: 1.0 },
      activeUsers:         { type: 'count', eps: 1.0 },
      newUsers:            { type: 'count', eps: 1.0 },
      paidConversions:     { type: 'count', eps: 1.0 },
      // 求和类：最大单用户贡献
      totalCreditsUsed:    { type: 'sum', eps: 1.0, maxContribution: 500 },
      totalRevenue:         { type: 'sum', eps: 1.0, maxContribution: 29900, precision: 2 },
      // 平均值类
      avgCreditsPerUser:    { type: 'average', eps: 1.0, maxValue: 500, minParticipants: 10, precision: 1 },
      avgExperimentsPerUser: { type: 'average', eps: 1.0, maxValue: 100, minParticipants: 10, precision: 1 },
    };

    const result = { ...report };
    result._dp_applied = true;
    result._dp_epsilon = epsilon;
    result._dp_timestamp = new Date().toISOString();

    for (const [key, config] of Object.entries(dpConfig)) {
      if (report[key] !== undefined && report[key] !== null) {
        result[key] = addNoise(
          report[key],
          config.eps,
          config.type,
          { maxContribution: config.maxContribution, maxValue: config.maxValue, 
            minParticipants: config.minParticipants, precision: config.precision }
        );
      }
    }

    return result;
  }

  /**
   * 隐私预算管理器 — 跟踪和分配epsilon
   * 防止通过多次查询累积信息推断个体数据
   */
  class PrivacyBudget {
    constructor(totalBudget = 5.0) {
      this.totalBudget = totalBudget;
      this.spentBudget = 0;
      this.queries = [];
    }

    /**
     * 请求epsilon分配
     * @param {string} queryName - 查询标识
     * @param {number} requested - 请求的epsilon
     * @returns {{ allowed: boolean, allocated: number, remaining: number }}
     */
    allocate(queryName, requested = 1.0) {
      const remaining = this.totalBudget - this.spentBudget;

      if (remaining <= 0) {
        return { allowed: false, allocated: 0, remaining: 0 };
      }

      const allocated = Math.min(requested, remaining);
      this.spentBudget += allocated;
      this.queries.push({
        name: queryName,
        epsilon: allocated,
        timestamp: Date.now()
      });

      return { allowed: true, allocated, remaining: this.totalBudget - this.spentBudget };
    }

    /**
     * 重置预算（新报告周期）
     */
    reset() {
      this.spentBudget = 0;
      this.queries = [];
    }

    /**
     * 获取使用情况摘要
     */
    getSummary() {
      return {
        totalBudget: this.totalBudget,
        spentBudget: this.spentBudget,
        remainingBudget: this.totalBudget - this.spentBudget,
        queryCount: this.queries.length,
        queries: this.queries
      };
    }
  }

  /**
   * 计算所需的参与者数量以达到给定精度
   * n >= 2 * sensitivity / (epsilon * accuracy)
   * @param {number} epsilon - 隐私预算
   * @param {number} accuracy - 期望精度（误差范围）
   * @param {number} sensitivity - 敏感性
   * @returns {number} 最小参与者数量
   */
  function minParticipantsForAccuracy(epsilon, accuracy, sensitivity = 1) {
    if (epsilon <= 0 || accuracy <= 0) return Infinity;
    return Math.ceil(2 * sensitivity / (epsilon * accuracy));
  }

  return {
    addNoise,
    anonymizeHistogram,
    anonymizeReport,
    PrivacyBudget,
    minParticipantsForAccuracy,
    // 暴露内部函数供测试
    _computeSensitivity,
    _laplaceSample
  };
})();

// ES Module 导出
export { DifferentialPrivacy };
export default DifferentialPrivacy;
