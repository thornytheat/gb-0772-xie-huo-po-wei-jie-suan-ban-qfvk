class DocketError(ValueError):
    def __init__(self, code, message, status=422):
        super().__init__(message)
        self.code, self.status = code, status


LABEL = {
    'WIDTH':'表单列数错误','BLANK':'空白记录','TIMESTAMP':'时间戳无效','INTERVAL':'区间倒置',
    'UNKNOWN_BERTH':'泊位不存在','RANGE':'窗外记录','UNSET':'字段缺失','DUPLICATE':'重复记录',
    'ID_CONFLICT':'同编号冲突','PLATE':'车牌格式错误','BAD_STATUS':'预约状态无效',
    'PLATE_OVERLAP':'多个车在同一泊位','BERTH_OVERLAP':'同车同时出现在多个泊位',
    'FREE':'预约首免','BASE':'基础价','UPGRADE':'升级价','CANCELLED':'到达前预约撤销',
    'BOOKED':'预约已关联','WALK_IN':'无生效预约','GAP':'实收差价','MATCH':'实收一致',
    'NO_PAYMENT':'无实收回执','ORPHAN':'游离实收','ATTACH_CONFLICT':'预约版本冲突',
    'AMOUNT':'金额无效','UNKNOWN_SENSOR':'杆侧来源缺失','NONE':'无占位',
    'REBOOKED':'已获准换泊','REBOOK_MISMATCH':'未按获准泊位或时段停靠',
    'WAITING':'等待可停泊位','NO_CHOICE':'无可换泊位','ONLY_BLOCKED_CHOICES':'候选时段均被占用',
    'APPROVED':'换泊已获准','CONFIRMED':'换泊已确认','FULFILLED':'已按获准安排兑现',
    'REBOOK_MISSING':'换泊确认不存在','REBOOK_NOT_APPROVED':'换泊尚未获准',
}
