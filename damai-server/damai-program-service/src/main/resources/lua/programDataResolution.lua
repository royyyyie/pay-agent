-- 票档数量数据
local ticket_category_list = cjson.decode(ARGV[1])
-- 如果是订单创建，那么就扣除未售卖的座位id
-- 如果是订单取消，那么就扣除锁定的座位id
local del_seat_list = cjson.decode(ARGV[2])
-- 如果是订单创建的操作，那么添加到锁定的座位数据
-- 如果是订单订单的操作，那么添加到未售卖的座位数据
local add_seat_data_list = cjson.decode(ARGV[3])
-- 如果是订单创建，则扣票档数量
-- 如果是订单取消，则恢复票档数量
for index,increase_data in ipairs(ticket_category_list) do
     -- 票档数量的key
    local program_ticket_remain_number_hash_key = increase_data.programTicketRemainNumberHashKey
     -- 票档id
    local ticket_category_id = increase_data.ticketCategoryId
     -- 扣除的数量
    local increase_count = increase_data.count
    redis.call('HINCRBY',program_ticket_remain_number_hash_key,ticket_category_id,increase_count)
end
-- 如果是订单创建,将没有售卖的座位删除，再将座位数据添加到锁定的座位中
-- 如果是订单取消,将锁定的座位删除，再将座位数据添加到没有售卖的座位中
for index, seat in pairs(del_seat_list) do
     -- 要去除的座位对应的hash的键
    local seat_hash_key_del = seat.seatHashKeyDel
       -- 座位id集合
    local seat_id_list = seat.seatIdList
    redis.call('HDEL',seat_hash_key_del,unpack(seat_id_list))
end
for index, seat in pairs(add_seat_data_list) do
     -- 要添加的座位对应的hash的键
    local seat_hash_key_add = seat.seatHashKeyAdd
     -- 作为数据
    local seat_data_list = seat.seatDataList
    redis.call('HMSET',seat_hash_key_add,unpack(seat_data_list))
end